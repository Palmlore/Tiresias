"""Regenerate the offline sample in data/sample/ from public SEC EDGAR endpoints.

Usage (from the repo root):
    uv run --project services/agent python scripts/fetch_sample.py            # fetch from SEC
    uv run --project services/agent python scripts/fetch_sample.py --from DIR # trim local raw copies

Needs SEC_USER_AGENT (an email contact, per SEC fair access) in the environment or .env when fetching.
With --from, DIR holds raw responses named <entity>_companyfacts.json, <entity>_submissions.json and
company_tickers.json; nothing is fetched.

The trim is fixed and not case-specific: the same core income-statement concepts for every entity,
10-K and 8-K rows of the filing history, and ticker-map rows for the GE family. PROVENANCE.json
records each file's source URL, fetch time (UTC), the SHA-256 of the raw response and of the trimmed file.
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sample"

ENTITIES = {  # name -> CIK (checked 2026-10-22, docs/data-sources.md)
    "ge": "0000040545",
    "gehc": "0001932393",
    "gev": "0001996810",
}
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
                ua = line.partition("=")[2].split(" #")[0].strip()
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
    return {k: v for k, v in raw.items() if TICKER_TITLE.match(v["title"])}


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
    args = parser.parse_args()
    out = args.out
    src = Source(args.local)
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for name, cik in ENTITIES.items():
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
        records.append(
            write(
                out,
                f"{name}_submissions.json",
                url,
                raw,
                fetched,
                trim_submissions(json.loads(raw)),
                f"recent filings, forms {sorted(FORMS)}",
            )
        )
    raw, fetched = src.get(URLS["tickers"], "company_tickers.json")
    records.append(
        write(
            out,
            "company_tickers.json",
            URLS["tickers"],
            raw,
            fetched,
            trim_tickers(json.loads(raw)),
            f"titles matching {TICKER_TITLE.pattern}",
        )
    )
    provenance = {
        "generated_by": "scripts/fetch_sample.py",
        "generated_at": now_utc(),
        "files": records,
    }
    (out / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"✔ PROVENANCE.json: {len(records)} files")


if __name__ == "__main__":
    main()
