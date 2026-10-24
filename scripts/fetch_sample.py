"""Regenerate the offline sample in data/sample/ from public SEC EDGAR endpoints.

Usage (from the repo root):
    uv run --project services/agent python scripts/fetch_sample.py                 # fetch from SEC
    uv run --project services/agent python scripts/fetch_sample.py --only mmm,solv # some entities
    uv run --project services/agent python scripts/fetch_sample.py --from DIR      # trim raw copies

Needs SEC_USER_AGENT (an email contact, per SEC fair access) in the environment or .env when fetching.
With --from, DIR holds raw responses named <entity>_companyfacts.json, <entity>_submissions.json,
company_tickers.json and fts_<hash>.json (one per full-text search); nothing is fetched. With --only,
the other entities' files and their PROVENANCE.json records are left as they are (the ticker map is
always rebuilt).

The trim is fixed and not case-specific: the same core income-statement concepts for every entity,
10-K and 8-K rows of the filing history, ticker-map rows for the sample entities (and the GE family),
and, for each disposal listed in FULLTEXT, the EDGAR full-text search the source scout runs for it
(catalog ``sec_fulltext``; phrase, forms and date window computed by the scout's own functions),
trimmed to the fields 40_fulltext_total.sql / 41_fulltext_hits.sql read. PROVENANCE.json records
each file's source URL, fetch time (UTC), the SHA-256 of the raw response and of the trimmed file.
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services" / "agent" / "src"))
from tiresias_agent.adapters.catalog import (
    _profile,
    fulltext_endpoint,
    fulltext_sample_file,
    fulltext_table,
    load_catalog,
)
from tiresias_agent.agents.knowledge import DISPOSAL, REGISTRATION_FORMS
from tiresias_agent.agents.source_scout import (
    REGISTRATION_LOOKBACK_DAYS,
    search_phrase,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sample"

ENTITIES = {  # name -> CIK (checked 2026-10-22 and 2026-10-24, docs/data-sources.md)
    "ge": "0000040545",
    "gehc": "0001932393",
    "gev": "0001996810",
    "mmm": "0000066740",  # 3M
    "solv": "0001964738",  # Solventum, spun off from 3M
}
# Disposals (8-K item 2.01 filing dates of the source entity) whose registration search by the
# source scout is cached for sample mode. The search itself (phrase, forms, window) is computed
# by the scout's own functions, so the cached file is the response live mode reads.
FULLTEXT = {"mmm": ["2024-04-04"]}
FULLTEXT_FIELDS = ["ciks", "display_names", "file_date", "form", "items"]
CONCEPTS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "IncomeLossFromContinuingOperations",
    "IncomeLossFromDiscontinuedOperationsNetOfTax",
    "DisposalGroupIncludingDiscontinuedOperationRevenue",
    "GoodwillImpairmentLoss",
]
FORMS = {"10-K", "10-K/A", "8-K", "8-K/A"}
SUBMISSION_COLUMNS = [
    "accessionNumber",
    "filingDate",
    "reportDate",
    "form",
    "items",
    "primaryDocument",
]
TICKER_TITLE = re.compile(r"^(GE\b|General Electric)", re.IGNORECASE)

URLS = {
    "companyfacts": "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    "submissions": "https://data.sec.gov/submissions/CIK{cik}.json",
    "tickers": "https://www.sec.gov/files/company_tickers.json",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_utc() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    env = ROOT / ".env"
    if not ua and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SEC_USER_AGENT="):
                ua = line.partition("=")[2].split(" #")[0].strip().strip("'\"")
    if not ua or "@" not in ua:
        sys.exit(
            "✖ SEC_USER_AGENT must be set to 'Name email@example.com' (SEC fair-access policy)"
        )
    return ua


class Source:
    """Raw responses from SEC, or from a local directory with --from."""

    def __init__(self, local: pathlib.Path | None):
        self.local = local
        self.client = (
            None
            if local
            else httpx.Client(
                headers={"User-Agent": user_agent()}, timeout=60, follow_redirects=True
            )
        )

    def get(self, url: str, local_name: str) -> tuple[bytes, str]:
        if self.local:
            path = self.local / local_name
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC)
            return path.read_bytes(), mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self.client.get(url)
        resp.raise_for_status()
        time.sleep(0.2)  # SEC allows at most 10 requests/s
        return resp.content, now_utc()


def trim_companyfacts(raw: dict) -> dict:
    gaap = raw.get("facts", {}).get("us-gaap", {})
    kept = {
        c: {"label": gaap[c].get("label"), "units": {"USD": gaap[c]["units"]["USD"]}}
        for c in CONCEPTS
        if "USD" in gaap.get(c, {}).get("units", {})
    }
    return {"cik": raw["cik"], "entityName": raw["entityName"], "facts": {"us-gaap": kept}}


def trim_submissions(raw: dict) -> dict:
    recent = raw["filings"]["recent"]
    rows = [i for i, form in enumerate(recent["form"]) if form in FORMS]
    return {
        "cik": raw["cik"],
        "name": raw["name"],
        "tickers": raw.get("tickers", []),
        "sic": raw.get("sic"),
        "formerNames": raw.get("formerNames", []),
        "filings": {"recent": {col: [recent[col][i] for i in rows] for col in SUBMISSION_COLUMNS}},
    }


def trim_tickers(raw: dict) -> dict:
    ciks = {int(c) for c in ENTITIES.values()}
    return {
        k: v for k, v in raw.items() if TICKER_TITLE.match(v["title"]) or int(v["cik_str"]) in ciks
    }


def trim_fulltext(raw: dict) -> dict:
    """The search response as 40/41_fulltext_*.sql read it: hits.total and, per hit, the _source
    fields the aggregation uses (plus _id, accession and document, for provenance)."""
    hits = raw.get("hits") or {}
    return {
        "hits": {
            "total": hits.get("total"),
            "hits": [
                {"_id": h.get("_id"), "_source": {k: h["_source"].get(k) for k in FULLTEXT_FIELDS}}
                for h in hits.get("hits") or []
            ],
        }
    }


def fulltext_search(name: str, tickers: dict, submissions: dict, disposal: str) -> str:
    """The endpoint the source scout requests for this disposal (its rules 1 and 2a)."""
    recent = submissions["filings"]["recent"]
    if not any(
        recent["filingDate"][i] == disposal
        and recent["form"][i].startswith("8-K")
        and set(str(recent["items"][i]).split(",")) & set(DISPOSAL.items)
        for i in range(len(recent["form"]))
    ):
        sys.exit(f"✖ {name}: no 8-K item {'/'.join(DISPOSAL.items)} filed {disposal}")
    cik = int(submissions["cik"])
    title = next(v["title"] for v in tickers.values() if int(v["cik_str"]) == cik)
    day = datetime.date.fromisoformat(disposal)
    phrase = search_phrase(title, _profile(submissions, f"{cik:010d}"), day)
    start = day - datetime.timedelta(days=REGISTRATION_LOOKBACK_DAYS)
    return fulltext_endpoint(
        load_catalog(), phrase, REGISTRATION_FORMS, start.isoformat(), disposal
    )


def write(
    out: pathlib.Path, name: str, url: str, raw: bytes, fetched: str, trimmed: dict, rule: str
) -> dict:
    body = (json.dumps(trimmed, indent=1, sort_keys=True) + "\n").encode()
    (out / name).write_bytes(body)
    print(f"✔ {name}: {len(raw):,} B raw → {len(body):,} B")
    return {
        "file": name,
        "source_url": url,
        "fetched_at": fetched,
        "raw_sha256": sha256(raw),
        "sha256": sha256(body),
        "trim": rule,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="local", type=pathlib.Path, help="trim raw files from DIR")
    parser.add_argument(
        "--out", type=pathlib.Path, default=OUT, help="output dir (default data/sample)"
    )
    parser.add_argument(
        "--only", help=f"comma-separated entities to refresh (of {', '.join(ENTITIES)})"
    )
    args = parser.parse_args()
    out = args.out
    only = [n.strip() for n in args.only.split(",")] if args.only else list(ENTITIES)
    unknown = [n for n in only if n not in ENTITIES]
    if unknown:
        sys.exit(f"✖ unknown entities {unknown}")
    src = Source(args.local)
    out.mkdir(parents=True, exist_ok=True)
    records = []
    submissions = {}
    for name in only:
        cik = ENTITIES[name]
        url = URLS["companyfacts"].format(cik=cik)
        raw, fetched = src.get(url, f"{name}_companyfacts.json")
        records.append(
            write(
                out,
                f"{name}_companyfacts.json",
                url,
                raw,
                fetched,
                trim_companyfacts(json.loads(raw)),
                f"us-gaap USD concepts: {', '.join(CONCEPTS)}",
            )
        )
        url = URLS["submissions"].format(cik=cik)
        raw, fetched = src.get(url, f"{name}_submissions.json")
        submissions[name] = trim_submissions(json.loads(raw))
        records.append(
            write(
                out,
                f"{name}_submissions.json",
                url,
                raw,
                fetched,
                submissions[name],
                f"recent filings, forms {sorted(FORMS)}",
            )
        )
    raw, fetched = src.get(URLS["tickers"], "company_tickers.json")
    tickers = trim_tickers(json.loads(raw))
    records.append(
        write(
            out,
            "company_tickers.json",
            URLS["tickers"],
            raw,
            fetched,
            tickers,
            f"titles matching {TICKER_TITLE.pattern}, or CIKs of the sample entities",
        )
    )
    for name in (n for n in only if n in FULLTEXT):
        for disposal in FULLTEXT[name]:
            url = fulltext_search(name, tickers, submissions[name], disposal)
            file = fulltext_sample_file(fulltext_table(url))
            raw, fetched = src.get(url, file)
            records.append(
                write(
                    out,
                    file,
                    url,
                    raw,
                    fetched,
                    trim_fulltext(json.loads(raw)),
                    f"source scout registration search for the {name} disposal filed {disposal}; "
                    f"hits.total, and _id + _source.{{{', '.join(FULLTEXT_FIELDS)}}} per hit",
                )
            )
    written = {r["file"] for r in records}
    previous = out / "PROVENANCE.json"
    kept = []
    if args.only and previous.exists():
        kept = [r for r in json.loads(previous.read_text())["files"] if r["file"] not in written]
    provenance = {
        "generated_by": "scripts/fetch_sample.py",
        "generated_at": now_utc(),
        "files": kept + records,
    }
    previous.write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"✔ PROVENANCE.json: {len(kept) + len(records)} files ({len(records)} refreshed)")


if __name__ == "__main__":
    main()
