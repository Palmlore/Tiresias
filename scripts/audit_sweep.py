#!/usr/bin/env python3
"""Independent correctness audit of the coverage sweep (docs/measurements/coverage-sweep-2026-10-26.md).

Offline tooling, like scripts/fetch_sample.py: it reads SEC EDGAR directly (companyfacts,
submissions, the cited 8-K documents) and never goes through Tiresias, Zetaris or a model, so
it checks the sweep's answers against the source rather than against the pipeline that made
them. The claims it checks (figures, periods, counterparties, cited filings) are copied below
from the sweep traces, one CASE per company; every check is recomputed from SEC data.

Checks per answered company:
  a  the parent's original and restated figures match SEC companyfacts (value, period, 10-K)
  b  the counterparty (named or implied) really is the business separated from the parent
  c  the counterparty's own figure for the same period matches the add-back
  d  the add-back arithmetic and residual recompute
  e  every filing cited in the action is the separation (its document names the counterparty)
Frontier companies get one check, f: was there a separation in the window, and was the data
needed to answer it in structured XBRL (i.e. is "no cause asserted" a correct stop or a miss)?

Needs SEC_USER_AGENT (an email contact, per SEC fair access) in the environment or .env.
Responses are cached under --cache (default ../workspace/sec-audit-cache, outside the repo).

    python3 scripts/audit_sweep.py [--out docs/measurements/coverage-sweep-2026-10-26-audit.jsonl]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.request
from html import unescape

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT.parent / "workspace" / "sec-audit-cache"
DEFAULT_OUT = ROOT / "docs" / "measurements" / "coverage-sweep-2026-10-26-audit.jsonl"
REV = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]
DISPOSAL = "DisposalGroupIncludingDiscontinuedOperationRevenue"
TOLERANCE = 0.05  # the sweep's reconciliation tolerance (share of the original figure)
B = 1e9

# Claims from the sweep traces (values as the agent printed them, in $B, two decimals).
CASES = [
    {
        "ticker": "IBM",
        "parent_cik": 51143,
        "period": ("2019-01-01", "2019-12-31"),
        "label": "FY2019",
        "original": (77.15, "2021-02-23"),
        "restated": (57.71, "2022-02-22"),
        "counterparty": {"name": "Kyndryl", "cik": 1867072, "value": 20.28, "filed": "2022-03-10"},
        "residual": -0.0435,
        "cited": [("2021-11-04", "2.01,7.01,9.01")],
    },
    {
        "ticker": "BBWI",
        "parent_cik": 701985,
        "period": ("2019-02-03", "2020-02-01"),
        "label": "FY2020",
        "original": (12.91, "2021-03-19"),
        "restated": (5.41, "2022-03-18"),
        "counterparty": {
            "name": "Victoria's Secret",
            "cik": 1856437,
            "value": 7.51,
            "filed": "2022-03-18",
        },
        "residual": 0.0,
        "cited": [("2021-08-03", "1.01,2.01,5.02,5.03,8.01,9.01")],
    },
    {
        "ticker": "FTV",
        "parent_cik": 1659166,
        "period": ("2023-01-01", "2023-12-31"),
        "label": "FY2023",
        "original": (6.07, "2025-02-25"),
        "restated": (3.91, "2026-02-25"),
        "counterparty": {"name": "Ralliant", "cik": 2041385, "value": 2.16, "filed": "2026-02-26"},
        "residual": -0.002,
        "cited": [("2025-06-30", "1.01,2.01,2.02,5.02,8.01,9.01")],
    },
    {
        "ticker": "DD",
        "parent_cik": 1666700,
        "period": ("2024-01-01", "2024-12-31"),
        "label": "FY2024",
        "original": (12.39, "2025-02-14"),
        "restated": (6.72, "2026-02-17"),
        "counterparty": {"name": "Qnity", "cik": 2058873, "value": 4.33, "filed": "2026-02-26"},
        "residual": 0.235,
        "cited": [("2025-11-03", "1.01,2.01,5.02,7.01,8.01,9.01")],
    },
    {
        # Loop A only: the share is measured on the parent's own disposal-group fact; the
        # counterparty is not named by the agent, so b/c test the business that was separated.
        "ticker": "JNJ",
        "parent_cik": 200406,
        "period": ("2021-01-04", "2022-01-02"),
        "label": "FY2022",
        "original": (93.78, "2023-02-16"),
        "restated": (78.74, "2024-02-16"),
        "disposal_fact": (15.04, "2024-02-16"),
        "counterparty": {"name": "Kenvue", "cik": 1944048, "value": None, "filed": None},
        "residual": 0.0,
        "cited": [("2022-12-22", "1.01,2.01,7.01,9.01"), ("2023-08-25", "2.01")],
    },
    {
        "ticker": "MRK",
        "parent_cik": 310158,
        "period": ("2019-01-01", "2019-12-31"),
        "label": "FY2019",
        "original": (46.84, "2021-02-25"),
        "restated": (39.12, "2022-02-25"),
        "disposal_fact": (7.72, "2022-02-25"),
        "counterparty": {"name": "Organon", "cik": 1821825, "value": None, "filed": None},
        "residual": 0.0,
        "cited": [("2021-06-03", "2.01,9.01")],
    },
]

# Evidence-frontier rows: the question as framed, and the separation we look for in the window.
FRONTIER = [
    {
        "ticker": "DHR",
        "parent_cik": 313616,
        "period": ("2022-01-01", "2022-12-31"),
        "original": (31.47, "2023-02-22"),
        "restated": (26.64, "2024-02-21"),
        "candidates": [("Veralto", 1967680)],
    },
    {
        "ticker": "RTX",
        "parent_cik": 101829,
        "period": ("2018-01-01", "2018-12-31"),
        "original": (66.50, "2020-02-06"),
        "restated": (34.70, "2021-02-08"),
        "candidates": [("Carrier", 1783180), ("Otis", 1781335)],
    },
    {
        "ticker": "PFE",
        "parent_cik": 78003,
        "period": ("2023-01-01", "2023-12-31"),
        "original": (50.91, "2024-02-22"),
        "restated": (59.55, "2025-02-27"),
        "candidates": [],
    },
    {
        "ticker": "IP",
        "parent_cik": 51434,
        "period": ("2023-01-01", "2023-12-31"),
        "original": (18.92, "2025-02-21"),
        "restated": (16.03, "2026-02-27"),
        "candidates": [],
    },
    {
        "ticker": "BDX",
        "parent_cik": 10795,
        "period": ("2020-10-01", "2021-09-30"),
        "original": (20.25, "2021-11-24"),
        "restated": (19.13, "2022-11-22"),
        "candidates": [("Embecta", 1872789)],
    },
]


class Sec:
    def __init__(self, cache: pathlib.Path, ua: str) -> None:
        self.cache, self.ua, self.last = cache, ua, 0.0
        cache.mkdir(parents=True, exist_ok=True)
        try:
            import certifi  # the uv venv has it; the framework Python may lack a CA bundle

            self.ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self.ctx = ssl.create_default_context()

    def raw(self, url: str) -> bytes:
        path = self.cache / re.sub(r"[^A-Za-z0-9.]", "_", url)
        if path.exists():
            return path.read_bytes()
        wait = 0.25 - (time.monotonic() - self.last)  # at most 4 requests a second
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": self.ua})
        data = urllib.request.urlopen(req, timeout=60, context=self.ctx).read()
        self.last = time.monotonic()
        path.write_bytes(data)
        return data

    def json(self, url: str):
        return json.loads(self.raw(url))

    def facts(self, cik: int):
        return self.json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")

    def subs(self, cik: int):
        return self.json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")


def annual(sec: Sec, cik: int, period, concepts, filed: str | None = None) -> list[dict]:
    """10-K facts for exactly this period (start, end), optionally from the 10-K filed that day."""
    gaap = sec.facts(cik)["facts"].get("us-gaap", {})
    out = []
    for concept in concepts:
        for u in gaap.get(concept, {}).get("units", {}).get("USD", []):
            if u.get("form") != "10-K" or (u.get("start"), u.get("end")) != tuple(period):
                continue
            if filed and u["filed"] != filed:
                continue
            out.append(
                {"concept": concept, "val": u["val"], "filed": u["filed"], "accn": u["accn"]}
            )
    return out


def find(rows: list[dict], claimed_b: float) -> dict | None:
    """The fact whose value rounds to the agent's two-decimal $B figure."""
    for r in rows:
        if abs(r["val"] / B - claimed_b) <= 0.005 + 1e-9:
            return r
    return None


def eight_k(sec: Sec, cik: int, date: str, items: str) -> dict | None:
    r = sec.subs(cik)["filings"]["recent"]
    for i, form in enumerate(r["form"]):
        if form == "8-K" and r["filingDate"][i] == date and r["items"][i] == items:
            return {"accn": r["accessionNumber"][i], "doc": r["primaryDocument"][i]}
    return None


def doc_text(sec: Sec, cik: int, accn: str, doc: str) -> str:
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{doc}"
    html = sec.raw(url).decode("utf-8", "replace")
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def registration(sec: Sec, cik: int) -> dict:
    s = sec.subs(cik)
    r = s["filings"]["recent"]
    forms = list(zip(r["form"], r["filingDate"], strict=True))
    form10 = sorted(d for f, d in forms if f.startswith("10-12"))
    s1 = sorted(d for f, d in forms if f in ("S-1", "S-1/A"))  # an IPO-first separation
    return {
        "name": s["name"],
        "former_names": [f["name"] for f in s.get("formerNames", [])],
        "form10_first": form10[0] if form10 else None,
        "s1_first": s1[0] if s1 else None,
    }


def frame(sec: Sec, cik: int, period, concept: str) -> str | None:
    """SEC's calendar frame for the period (e.g. CY2021), which names the year it mostly covers."""
    for u in sec.facts(cik)["facts"]["us-gaap"][concept]["units"]["USD"]:
        if (u.get("start"), u.get("end")) == tuple(period) and u.get("frame"):
            return u["frame"]
    return None


def verdict_answered(row: dict) -> dict:
    """correct / correct-with-caveat / wrong, from the checks (wrong = a figure or add-back fails)."""
    if any(row[k]["result"] == "fail" for k in "abcd"):
        return {"verdict": "wrong", "caveats": []}
    caveats = []
    fr = row["a"].get("sec_frame")
    if fr and fr.startswith("CY") and fr[2:] != row["a"]["label"][2:]:
        caveats.append(f"labelled {row['a']['label']}, SEC frame {fr}")
    if not row["b"]["named_by_agent"]:
        caveats.append("counterparty not named in the action")
    if row["d"].get("within_tolerance") is False:
        caveats.append("partial: residual outside the 5% tolerance")
    if any(c["item_2_01_is_an_acquisition"] for c in row["e"]["cited"]):
        caveats.append("an acquisition filing is cited as a candidate disposal")
    if any(row[k]["result"] == "unclear" for k in "bc"):
        caveats.append("counterparty not confirmed")
    return {"verdict": "correct with caveat" if caveats else "correct", "caveats": caveats}


def assess_frontier(row: dict) -> str:
    if not row["same_concept"]:
        return "not a restatement (two concepts)"
    if row.get("would_reconcile"):
        return "missed answer"
    return "correct stop"


def check(result: str, **detail) -> dict:
    return {"result": result, **detail}


def audit_answered(sec: Sec, case: dict) -> dict:
    cik, period = case["parent_cik"], case["period"]
    rows: dict = {"ticker": case["ticker"], "kind": "answered", "period": list(period)}
    orig = find(annual(sec, cik, period, REV, case["original"][1]), case["original"][0])
    rest = find(annual(sec, cik, period, REV, case["restated"][1]), case["restated"][0])
    days = (datetime.date.fromisoformat(period[1]) - datetime.date.fromisoformat(period[0])).days
    label_year = int(case["label"][2:])
    a = "pass" if orig and rest else "fail"
    rows["a"] = check(
        a,
        original=orig,
        restated=rest,
        label=case["label"],
        label_matches_period_end_year=label_year == int(period[1][:4]),
        sec_frame=frame(sec, cik, period, orig["concept"]) if orig else None,
        fiscal_year_days=days + 1,
    )
    delta = (rest["val"] - orig["val"]) if orig and rest else None

    cp = case["counterparty"]
    reg = registration(sec, cp["cik"])
    cp_rows = annual(sec, cp["cik"], period, REV)
    cp_own = cp_rows[0] if cp_rows else None
    rows["b"] = check(
        "pass" if (reg["form10_first"] or reg["s1_first"]) and cp_own else "unclear",
        counterparty=cp["name"],
        registrant=reg,
        named_by_agent=cp["value"] is not None,
    )
    if cp["value"] is not None:  # Loop B add-back on the counterparty's own figure
        used = find(annual(sec, cp["cik"], period, REV, cp["filed"]), cp["value"])
        rows["c"] = check("pass" if used else "fail", used=used)
        add = used["val"] if used else None
    else:  # Loop A: the parent's own disposal-group fact, corroborated by the counterparty
        fact = find(
            annual(sec, cik, period, [DISPOSAL], case["disposal_fact"][1]), case["disposal_fact"][0]
        )
        gap = (cp_own["val"] - fact["val"]) / fact["val"] if fact and cp_own else None
        rows["c"] = check(
            "pass" if fact and gap is not None and abs(gap) <= 0.01 else "unclear",
            parent_disposal_fact=fact,
            counterparty_own=cp_own,
            counterparty_vs_fact=round(gap, 4) if gap is not None else None,
        )
        add = fact["val"] if fact else None
    if delta is not None and add is not None:
        # the sweep's convention: the gap over the size of the effect (negative = over-explained)
        residual = (orig["val"] - (rest["val"] + add)) / abs(delta)
        within = abs(orig["val"] - (rest["val"] + add)) <= TOLERANCE * orig["val"]
        rows["d"] = check(
            "pass" if abs(residual - case["residual"]) <= 0.002 else "fail",
            effect=delta,
            add_back=add,
            gap=orig["val"] - (rest["val"] + add),
            residual=round(residual, 4),
            claimed_residual=case["residual"],
            within_tolerance=within,
        )
    else:
        rows["d"] = check("unclear")

    cited = []
    names = [cp["name"], *reg["former_names"], reg["name"]]
    for date, items in case["cited"]:
        k = eight_k(sec, cik, date, items)
        text = doc_text(sec, cik, k["accn"], k["doc"]) if k else ""
        low = text.lower().replace("\u2019", "'")
        hit = [n for n in names if re.split(r"[ ,']", n)[0].lower() in low]
        at = text.find("Item 2.01")
        item = text[at : at + 260] if at >= 0 else ""
        acquires = bool(
            re.search(r"(?i)tender offer to acquire|merger sub|acquired all of the", item)
        )
        cited.append(
            {
                "date": date,
                "items": items,
                "found": bool(k),
                "accn": k and k["accn"],
                "names_counterparty": bool(hit),
                "item_2_01_opening": item,
                "item_2_01_is_an_acquisition": acquires,
                "names_abiomed": "abiomed" in low,
            }
        )
    rows["e"] = check(
        "pass" if cited and all(c["names_counterparty"] for c in cited) else "caveat",
        cited=cited,
    )
    return rows


def audit_frontier(sec: Sec, case: dict) -> dict:
    cik, period = case["parent_cik"], case["period"]
    orig_all = annual(sec, cik, period, REV, case["original"][1])
    rest_all = annual(sec, cik, period, REV, case["restated"][1])
    orig, rest = find(orig_all, case["original"][0]), find(rest_all, case["restated"][0])
    row: dict = {"ticker": case["ticker"], "kind": "frontier", "period": list(period)}
    row["framed"] = {"original": orig, "restated": rest}
    row["same_concept"] = bool(orig and rest and orig["concept"] == rest["concept"])
    row["parent_concepts_in_original_10k"] = sorted({r["concept"] for r in orig_all})
    window = (case["original"][1], case["restated"][1])
    r = sec.subs(cik)["filings"]["recent"]
    row["filings_2_01_in_window"] = [
        (d, it)
        for f, d, it in zip(r["form"], r["filingDate"], r["items"], strict=True)
        if f == "8-K" and "2.01" in it and window[0] <= d <= window[1]
    ]
    cands = []
    total = 0.0
    for name, ccik in case["candidates"]:
        reg = registration(sec, ccik)
        own = annual(sec, ccik, period, REV)
        val = own[0]["val"] if own else None
        total += val or 0
        cands.append({"name": name, "registrant": reg, "own_figure": own[0] if own else None})
    row["candidates"] = cands
    if orig and rest and cands and all(c["own_figure"] for c in cands):
        gap = orig["val"] - (rest["val"] + total)
        row["would_reconcile"] = abs(gap) <= TOLERANCE * orig["val"]
        row["gap_if_added"] = gap
    return row


def ua_from_env() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "")
    env = ROOT / ".env"
    if not ua and env.exists():
        for line in env.read_text().splitlines():
            m = re.match(r"\s*SEC_USER_AGENT\s*=\s*(.*)", line)
            if m:
                ua = m.group(1).strip().strip("'\"")
    if not ua:
        sys.exit("SEC_USER_AGENT is not set (environment or .env)")
    return ua


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--cache", type=pathlib.Path, default=DEFAULT_CACHE)
    ap.add_argument("--env-file", type=pathlib.Path, help="read SEC_USER_AGENT from this .env")
    args = ap.parse_args(argv)
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            m = re.match(r"\s*SEC_USER_AGENT\s*=\s*(.*)", line)
            if m:
                os.environ["SEC_USER_AGENT"] = m.group(1).strip().strip("'\"")
    sec = Sec(args.cache, ua_from_env())
    rows = [audit_answered(sec, c) for c in CASES] + [audit_frontier(sec, c) for c in FRONTIER]
    for r in rows:
        if r["kind"] == "answered":
            r.update(verdict_answered(r))
        else:
            r["assessment"] = assess_frontier(r)
    args.out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    for r in rows:
        if r["kind"] == "answered":
            print(r["ticker"], {k: r[k]["result"] for k in "abcde"}, r["verdict"], r["caveats"])
        else:
            print(r["ticker"], "frontier:", r["assessment"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
