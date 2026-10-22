"""Measure local Zetaris: query latency, runtime REST-table registration, nested-JSON flattening.

Usage: python3 scripts/zetaris_probe.py   (reads ZETARIS_* and SEC_USER_AGENT from .env)

Creates objects prefixed `tiresias` in Zetaris (idempotent: "already exists" is reported, not fatal).
Prints one JSON line per statement: label, seconds, status, row count, first rows.
"""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.split(" #")[0].strip())


load_env()
URL = os.environ.get("ZETARIS_PROBE_URL", "http://localhost:8888/api/v1.0").rstrip("/")
KEY = os.environ["ZETARIS_API_KEY"]
ORG = os.environ.get("ZETARIS_ORG_ID", "1")
UA = os.environ.get("SEC_USER_AGENT", "Tiresias contact@example.com")


def run(label: str, sql: str, limit: int = 5, timeout: int = 300) -> dict:
    body = json.dumps(
        {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
    ).encode()
    req = urllib.request.Request(
        f"{URL}/sql-editor/sqls/run",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {KEY}",
            "X-Org-ID": ORG,
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read() or b"{}")
            rows = out.get("data") if isinstance(out, dict) else None
            result = {"status": "ok", "rows": len(rows or []), "sample": (rows or [])[:limit]}
    except urllib.error.HTTPError as err:
        text = err.read().decode(errors="replace")
        status = "exists" if "already exist" in text.lower() else f"http {err.code}"
        result = {"status": status, "error": text[:300]}
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        result = {"status": "error", "error": repr(err)[:300]}
    result = {"label": label, "seconds": round(time.perf_counter() - start, 2), **result}
    print(json.dumps(result, default=str), flush=True)
    return result


def concept_table(name: str, cik: str, concept: str) -> None:
    run(
        f"register {name}",
        f"""CREATE LIGHTNING REST TABLE {name}_raw FROM TIRESIAS_SEC REQUEST(
            endpoint "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json",
            method "get", response_type "json", http_encoding "URLENCODED"
        ) HEADER (user-agent "{UA}") BODY ()""",
    )
    run(
        f"view {name}",
        f"""CREATE SCHEMASTORE VIEW {name} WITH CONTAINER tiresias AS
            SELECT cik, `entityName` AS entity_name, tag AS concept,
                   fact.`start` AS period_start, fact.`end` AS period_end, fact.val AS value_usd,
                   fact.accn AS accn, fact.form AS form, fact.filed AS filed
            FROM tiresias_sec.{name}_raw LATERAL VIEW explode(units.USD) AS fact""",
    )


def main() -> int:
    for i in range(3):
        run(f"select-1 #{i + 1}", "SELECT 1 AS one")
    run("show lightning databases", "SHOW LIGHTNING DATABASES", limit=50)
    run("seeded sample query", "SELECT * FROM NY_TAXI.taxi_zones LIMIT 5")

    run(
        "create database",
        'CREATE LIGHTNING DATABASE TIRESIAS_SEC DESCRIBE BY "Tiresias SEC EDGAR REST"',
    )
    run("create container", "CREATE SCHEMASTORE CONTAINER tiresias")
    concept_table("ge_revenues", "0000040545", "Revenues")

    q = """SELECT period_start, period_end, value_usd, form, filed FROM tiresias.ge_revenues
           WHERE period_start = '2022-01-01' AND period_end = '2022-12-31' AND form = '10-K'
           ORDER BY filed"""
    run("GE 2022 revenue, 1st query (live fetch)", q)
    run("GE 2022 revenue, 2nd query", q)
    run("cache raw table", "CACHE TABLE tiresias_sec.ge_revenues_raw")
    run("GE 2022 revenue, after cache", q)

    # SEC's companyconcept endpoint returned empty units for GEHC on 2026-10-22 while companyfacts
    # held the values, so the reliable path is companyfacts + a struct-path view per concept.
    concept_table(
        "gehc_revenues", "0001932393", "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    run(
        "register GE companyfacts (whole file, ~6 MB)",
        f"""CREATE LIGHTNING REST TABLE ge_facts_raw FROM TIRESIAS_SEC REQUEST(
            endpoint "https://data.sec.gov/api/xbrl/companyfacts/CIK0000040545.json",
            method "get", response_type "json", http_encoding "URLENCODED"
        ) HEADER (user-agent "{UA}") BODY ()""",
    )
    run(
        "companyfacts: explode concept map",
        """SELECT count(*) AS concepts FROM (
             SELECT explode(from_json(to_json(facts.`us-gaap`),
               'map<string,struct<label:string>>')) AS (concept, meta)
             FROM tiresias_sec.ge_facts_raw)""",
    )
    run(
        "register GEHC companyfacts",
        f"""CREATE LIGHTNING REST TABLE gehc_facts_raw FROM TIRESIAS_SEC REQUEST(
            endpoint "https://data.sec.gov/api/xbrl/companyfacts/CIK0001932393.json",
            method "get", response_type "json", http_encoding "URLENCODED"
        ) HEADER (user-agent "{UA}") BODY ()""",
    )
    view = """CREATE SCHEMASTORE VIEW {name} WITH CONTAINER tiresias AS
        SELECT cik, entityName AS entity_name, '{concept}' AS concept,
               fact.`start` AS period_start, fact.`end` AS period_end, fact.val AS value_usd,
               fact.accn AS accn, fact.form AS form, fact.filed AS filed
        FROM tiresias_sec.{table} LATERAL VIEW explode(facts.`us-gaap`.{concept}.units.USD) AS fact"""
    run(
        "view ge_revenue_cf",
        view.format(name="ge_revenue_cf", concept="Revenues", table="ge_facts_raw"),
    )
    run(
        "view gehc_revenue",
        view.format(
            name="gehc_revenue",
            concept="RevenueFromContractWithCustomerExcludingAssessedTax",
            table="gehc_facts_raw",
        ),
    )
    run("cache GE companyfacts", "CACHE TABLE tiresias_sec.ge_facts_raw")
    run("cache GEHC companyfacts", "CACHE TABLE tiresias_sec.gehc_facts_raw")
    run(
        "reconcile GE restatement vs GEHC (cross-source join)",
        """WITH ge AS (
             SELECT filed, value_usd FROM tiresias.ge_revenue_cf
             WHERE period_start = '2022-01-01' AND period_end = '2022-12-31' AND form = '10-K'),
           hc AS (
             SELECT max(value_usd) AS gehc FROM tiresias.gehc_revenue
             WHERE period_start = '2022-01-01' AND period_end = '2022-12-31' AND form = '10-K')
           SELECT a.filed AS filed_a, b.filed AS filed_b, a.value_usd - b.value_usd AS delta,
                  hc.gehc, (a.value_usd - b.value_usd) - hc.gehc AS residual
           FROM ge a JOIN ge b ON a.filed < b.filed CROSS JOIN hc
           ORDER BY a.filed, b.filed""",
        limit=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
