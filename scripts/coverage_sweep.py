#!/usr/bin/env python3
"""Coverage sweep: run the pre-registered companies through a live Tiresias API, one by one.

The list, procedure and scoring are fixed in docs/measurements/coverage-sweep-2026-10-26.md
(committed before the first run). This script follows that procedure against a running agent
API in live mode (every read through Zetaris) and writes one JSON line per company: no trace
content, no secrets. Traces stay where the API writes them (LOGS_DIR, outside the repo).

    # API in live mode on the host (Zetaris up), e.g. on :8020, then:
    python3 scripts/coverage_sweep.py --api http://localhost:8020 --out results.jsonl [--only JNJ,DHR]

Stdlib only. Each HTTP call has a timeout; a company that fails is recorded, never skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# (ticker, separation year, expected separated registrants) from the pre-registered table.
PARENTS = [
    ("JNJ", 2023, ["Kenvue"]),
    ("DHR", 2023, ["Veralto"]),
    ("K", 2023, ["WK Kellogg"]),
    ("IBM", 2021, ["Kyndryl"]),
    ("MRK", 2021, ["Organon"]),
    ("RTX", 2020, ["Carrier", "Otis"]),
    ("FTV", 2020, ["Vontier"]),
    ("BBWI", 2021, ["Victoria's Secret", "Victorias Secret"]),
    ("PFE", 2020, ["Viatris", "Upjohn"]),
    ("DD", 2019, ["Dow", "Corteva"]),
    ("IP", 2021, ["Sylvamo"]),
    ("BDX", 2022, ["Embecta"]),
]
CONTROLS = [("KHC", 2018), ("COST", 2023)]
FORECAST = {
    "question": "What will Apple's revenue be in FY2030?",
    "entity": {"ticker": "AAPL"},
    "metric": "Revenues",
    "period": {"start": "2030-01-01", "end": "2030-12-31"},
    "asker": {"role": "analyst"},
}
OPTIONS = {"hitl_mode": "auto", "memory": "cold", "depth": "quick"}
RUN_TIMEOUT = 700  # RUN_BUDGET_SECONDS (600) + the API's 30 s wait, plus slack


def call(api: str, method: str, path: str, body: dict | None = None, timeout: float = 60):
    req = urllib.request.Request(
        api + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"null")
        except ValueError:
            return err.code, None


def resolve(api: str, ticker: str) -> dict | None:
    q = urllib.parse.quote(ticker)
    status, body = call(api, "GET", f"/entities?q={q}&sample_mode=false&limit=10")
    if status != 200 or not body:
        return None
    for e in body.get("entities", []):
        if (e.get("ticker") or "").upper() == ticker.upper():
            return e
    return None


def zetaris_state(api: str) -> str:
    status, body = call(api, "GET", "/health", timeout=15)
    if status != 200 or not body:
        return f"health {status}"
    return (body.get("zetaris") or {}).get("state") or "unknown"


def bootstrap(api: str, entity: dict, year: int) -> dict:
    """The neutral run that registers the company (not scored)."""
    req = {
        "question": f"Why did {entity['ticker']}'s Revenues change in FY{year}?",
        "entity": {"ticker": entity["ticker"], "cik": entity["cik"]},
        "metric": "Revenues",
        "period": {"start": f"{year}-01-01", "end": f"{year}-12-31"},
        "asker": {"role": "analyst"},
        "options": OPTIONS,
    }
    t = time.monotonic()
    status, body = call(api, "POST", "/run", req, timeout=RUN_TIMEOUT)
    return {
        "http": status,
        "end_state": (body or {}).get("end_state"),
        "seconds": round(time.monotonic() - t, 1),
    }


def pick(api: str, cik: str, restatement_first: bool) -> tuple[dict | None, str]:
    status, body = call(
        api, "GET", f"/entities/{cik}/suggestions?role=analyst&limit=12&sample_mode=false"
    )
    if status != 200 or not body:
        return (
            None,
            f"suggestions HTTP {status}: {(body or {}).get('message') if isinstance(body, dict) else ''}",
        )
    sugg = body.get("suggestions") or []
    if not sugg:
        return None, "no suggestion"
    if restatement_first:
        for s in sugg:
            if s.get("kind") == "restatement":
                return s, "first restatement"
        return sugg[0], "no restatement suggested; first suggestion"
    for s in sugg:
        if s.get("kind") != "restatement":
            return s, "first non-restatement"
    return sugg[0], "only restatements; first suggestion"


def trace_facts(path: str | None) -> dict:
    """Counterparty, registrations and sample reads, from the run's own trace (not committed)."""
    out = {"counterparty": None, "counterparty_cik": None, "sample_reads": 0, "trace_lines": 0}
    if not path or not Path(path).is_file():
        return out
    for raw in Path(path).read_text().splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        out["trace_lines"] += 1
        if line.get("mode") == "sample" or line.get("source") == "sample":
            out["sample_reads"] += 1
        if line.get("agent") == "source_scout" and line.get("action") == "resolve":
            ent = line.get("entity") or {}
            if ent.get("name"):
                out["counterparty"], out["counterparty_cik"] = ent.get("name"), ent.get("cik")
    return out


def accepted_loop_b(result: dict) -> dict | None:
    links = result.get("why_tree") or []
    best = None
    for link in links:
        evidenced = link.get("verdict") == "accepted" or link.get("evidence_status") == "evidenced"
        deeper = best is None or (link.get("depth") or 0) >= (best.get("depth") or 0)
        if evidenced and link.get("parent_id") and deeper:
            best = link
    return best


def run_one(api: str, request: dict) -> tuple[dict | None, float, int]:
    req = dict(request)
    req["options"] = {**(req.get("options") or {}), **OPTIONS}
    t = time.monotonic()
    status, body = call(api, "POST", "/run", req, timeout=RUN_TIMEOUT)
    return body if isinstance(body, dict) else None, round(time.monotonic() - t, 1), status


def record(
    api: str, ticker: str, year: int | None, expected: list[str] | None, control: bool
) -> dict:
    row: dict = {
        "ticker": ticker,
        "group": "control" if control else "spin-off",
        "zetaris_before": zetaris_state(api),
    }
    entity = resolve(api, ticker)
    if entity is None:
        row["outcome"] = "not resolved"
        return row
    row.update(cik=entity["cik"], name=entity.get("name"), tier_before=entity.get("tier"))
    if entity.get("tier") != "ready" and year is not None:
        row["bootstrap"] = bootstrap(api, entity, year)
        again = resolve(api, ticker) or {}
        row["tier_after_bootstrap"] = again.get("tier")
    s, why = pick(api, entity["cik"], restatement_first=not control)
    row["selection"] = why
    if s is None:
        row["outcome"] = "no question"
        return row
    row.update(kind=s.get("kind"), question=s.get("question"))
    result, secs, status = run_one(api, s["request"])
    row.update(http=status, seconds=secs)
    if result is None:
        row["outcome"] = "error"
        return row
    stats = result.get("stats") or {}
    link = accepted_loop_b(result) if result.get("end_state") == "actionable" else None
    facts = trace_facts(result.get("trace_file"))
    row.update(
        outcome=result.get("end_state"),
        status=result.get("status"),
        error=(result.get("error") or {}).get("type"),
        hops=stats.get("hops"),
        queries=stats.get("queries"),
        sources_onboarded=stats.get("sources_onboarded"),
        run_seconds=stats.get("seconds"),
        share_explained=link.get("share_explained") if link else None,
        residual=(
            result.get("residual")
            if result.get("residual") is not None
            else (link or {}).get("residual")
        ),
        counterparty=facts["counterparty"],
        counterparty_cik=facts["counterparty_cik"],
        sample_reads=facts["sample_reads"],
        trace_lines=facts["trace_lines"],
        trace=Path(result["trace_file"]).name if result.get("trace_file") else None,
    )
    if expected is not None:
        cp = (facts["counterparty"] or "").lower()
        row["expected_counterparty"] = expected
        row["counterparty_matches"] = bool(cp) and any(x.lower().split()[0] in cp for x in expected)
    row["zetaris_after"] = zetaris_state(api)
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8020")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    only = {t.strip().upper() for t in args.only.split(",") if t.strip()}
    out = Path(args.out)
    plan = [(t, y, e, False) for t, y, e in PARENTS] + [(t, y, None, True) for t, y in CONTROLS]
    with out.open("a") as fh:
        for ticker, year, expected, control in plan:
            if only and ticker not in only:
                continue
            row = record(args.api, ticker, year, expected, control)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in ("ticker", "outcome", "counterparty", "residual", "seconds")
                    }
                )
            )
        if not only or "AAPL" in only:
            result, secs, status = run_one(args.api, FORECAST)
            row = {
                "ticker": "AAPL",
                "group": "control",
                "question": FORECAST["question"],
                "selection": "fixed forecast question",
                "http": status,
                "seconds": secs,
                "outcome": (result or {}).get("end_state"),
                "in_scope": ((result or {}).get("scope") or {}).get("in_scope"),
                "scope_reasons": [
                    r.get("type") for r in ((result or {}).get("scope") or {}).get("reasons") or []
                ],
            }
            fh.write(json.dumps(row) + "\n")
            print(json.dumps({k: row.get(k) for k in ("ticker", "outcome", "in_scope", "seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
