"""A stateful fake of the Zetaris SQL endpoint for live-mode tests (httpx.MockTransport).

It understands the statements rendered from zetaris/sql/ (registration DDL, SHOW, the concept,
filings, sec_tickers and submissions queries) and answers them from data/sample/ under other
names: ``aliases`` maps a fake object ticker (e.g. "acme") to a sample prefix (e.g. "ge"), so a
company outside the showcase set can be exercised end to end without a network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from tiresias_agent.adapters import zetaris as z

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "sample"
SLUG_TO_CONCEPT = {v: k for k, v in z.CONCEPT_SLUGS.items()}


def ok(columns, rows):
    return httpx.Response(200, json={"headers": columns, "data": rows})


def bad(msg):
    return httpx.Response(400, json={"msg": msg})


class FakeZetaris:
    def __init__(self, tickers, aliases, *, tables=None, views=None, fail_all=False):
        self.tickers = tickers  # [(ticker, cik_int, name)]
        self.aliases = aliases  # object ticker -> sample prefix
        self.tables = dict(tables or {})  # name -> endpoint
        self.views = set(views or ())
        self.fail_all = fail_all
        self.sql: list[str] = []

    def _load(self, prefix, kind):
        return json.loads((SAMPLE / f"{prefix}_{kind}.json").read_text())

    def _facts(self, ticker, concept):
        data = self._load(self.aliases[ticker], "companyfacts")
        return data["facts"]["us-gaap"].get(concept)

    def client(self, tracer=None):
        cfg = z.ZetarisConfig(rest_url="http://zetaris.test/api/v1.0", api_key="k", max_retries=0)
        return z.ZetarisClient(
            cfg, tracer=tracer, transport=httpx.MockTransport(self), sleep=lambda s: None
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["sql"]
        self.sql.append(sql)
        if self.fail_all:
            return httpx.Response(503, text="down")
        s = " ".join(sql.split())
        if s.startswith("SHOW DATASOURCE VIEWS"):
            return ok(["table_identifier"], [[v] for v in sorted(self.views)])
        if s.startswith("SHOW LIGHTNING TABLES"):
            return ok(["name", "source_table"], [[n, e] for n, e in sorted(self.tables.items())])
        if s.startswith(("CREATE LIGHTNING DATABASE", "CREATE SCHEMASTORE CONTAINER")):
            return bad("An object named X already exists.")
        if s.startswith("CACHE TABLE"):
            return ok([], [])
        m = re.match(
            r'CREATE LIGHTNING REST TABLE (\w+) FROM TIRESIAS_SEC REQUEST\( endpoint "([^"]+)"', s
        )
        if m:
            if m.group(1) in self.tables:
                return bad(f'A table named "{m.group(1)}" already exists.')
            self.tables[m.group(1)] = m.group(2)
            return ok([], [])
        m = re.match(r"CREATE OR REPLACE SCHEMASTORE VIEW (\w+) WITH CONTAINER tiresias", s)
        if m:
            name = m.group(1)
            concept = re.search(r"SELECT '(\w+)' AS concept", s)
            if concept:
                ticker = name.split("_", 1)[0]
                if ticker not in self.aliases or self._facts(ticker, concept.group(1)) is None:
                    return bad(f"[FIELD_NOT_FOUND] No such struct field {concept.group(1)}")
            self.views.add(name)
            return ok([], [])
        if "FROM tiresias.sec_tickers" in s:
            return self._tickers(s)
        m = re.search(r"FROM tiresias_sec\.(\w+)_submissions_raw LATERAL VIEW OUTER", s)
        if m:
            return self._profile(m.group(1))
        if "FROM tiresias." in s and "_filings" in s:
            return self._filings(s)
        if s.startswith("SELECT concept"):
            return self._series(s)
        return bad(f"fake Zetaris does not understand: {s[:80]}")

    def _tickers(self, s):
        cols = ["cik", "name", "ticker", "exchange"]
        rows = [(t, c, n) for t, c, n in self.tickers]
        m = re.search(r"upper\(ticker\) IN \(([^)]*)\) OR cik IN \(([^)]*)\)", s)
        if m:
            syms = set(re.findall(r"'([^']+)'", m.group(1)))
            ciks = {int(x) for x in re.findall(r"\d+", m.group(2))}
            rows = [r for r in rows if r[0].upper() in syms or r[1] in ciks]
        m = re.search(r"IN \(([^)]*)\) ORDER", s) if "split(name" in s else None
        if m:
            words = set(re.findall(r"'([^']+)'", m.group(1)))
            rows = [r for r in rows if r[2].split()[0].lower() in words]
        m = re.search(r"LIKE '%([^%]*)%'", s)
        if m:
            rx = re.compile(re.escape(m.group(1)).replace("_", "."))
            rows = [r for r in rows if rx.search(r[2].lower())]
        rows.sort(key=lambda r: r[1], reverse=True)
        return ok(cols, [[str(c), n, t, "NYSE"] for t, c, n in rows])

    def _profile(self, ticker):
        cols = ["cik", "name", "tickers", "former_names", "accession_number", "form"]
        if ticker not in self.aliases:  # a registrant with no filings: one OUTER row
            return ok(
                [*cols, "filing_date", "items"], [["0", ticker, "[]", "[]", None, None, None, None]]
            )
        data = self._load(self.aliases[ticker], "submissions")
        recent = data["filings"]["recent"]
        head = [
            data["cik"],
            data["name"],
            json.dumps(data.get("tickers") or []),
            json.dumps(data.get("formerNames") or []),
        ]
        rows = [
            [*head, accn, recent["form"][i], recent["filingDate"][i], recent["items"][i]]
            for i, accn in enumerate(recent["accessionNumber"])
        ]
        return ok([*cols, "filing_date", "items"], rows)

    def _filings(self, s):
        ticker = re.search(r"FROM tiresias\.(\w+)_filings", s).group(1)
        forms = set(re.findall(r"'([^']+)'", s.split("WHERE form IN", 1)[1]))
        recent = self._load(self.aliases[ticker], "submissions")["filings"]["recent"]
        rows = [
            [
                accn,
                recent["filingDate"][i],
                recent["reportDate"][i],
                recent["form"][i],
                recent["items"][i],
                recent["primaryDocument"][i],
            ]
            for i, accn in enumerate(recent["accessionNumber"])
            if recent["form"][i] in forms
        ]
        rows.sort(key=lambda r: (r[1], r[0]), reverse=True)
        cols = ["accession_number", "filing_date", "report_date", "form", "items"]
        return ok([*cols, "primary_document"], rows)

    def _series(self, s):
        rows = []
        for ticker, slug in re.findall(r"FROM tiresias\.([a-z0-9]+?)_(\w+?)(?= |$)", s):
            concept = SLUG_TO_CONCEPT.get(slug)
            facts = self._facts(ticker, concept) if concept and ticker in self.aliases else None
            for f in ((facts or {}).get("units") or {}).get("USD") or []:
                rows.append(
                    [
                        concept,
                        "USD",
                        f["val"],
                        f.get("start"),
                        f["end"],
                        f.get("fy"),
                        f.get("fp"),
                        f.get("form"),
                        f.get("filed"),
                        f.get("accn"),
                        f.get("frame"),
                    ]
                )
        cols = ["concept", "unit", "val", "period_start", "period_end", "fy", "fp", "form"]
        return ok([*cols, "filed", "accn", "frame"], rows)
