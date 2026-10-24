"""Source scout: the counterparty of a disposal comes from filing evidence, not from its name
(BACKLOG B-5). Live discovery through the fake Zetaris, full-text search included."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fake_zetaris import FakeZetaris
from loop_helpers import RecordingTracer

from tiresias_agent.adapters.catalog import (
    FilingRow,
    Profile,
    SampleDiscovery,
    TickerRow,
    ZetarisDiscovery,
    fulltext_endpoint,
    fulltext_hit_rows,
    fulltext_sample_file,
    fulltext_table,
    load_catalog,
)
from tiresias_agent.adapters.zetaris import SourceError
from tiresias_agent.agents import source_scout
from tiresias_agent.models import EntityHint, InformationNeed, Period

CATALOG = load_catalog()
UA = "Tiresias test test@example.com"
DISPOSAL = "2023-01-04"  # the 8-K 2.01 date in the sample slice the fake answers from
# The source answers from one sample registrant; the counterparties from the others, under
# names that share nothing with the source's.
TICKERS = [
    ("ACME", 42, "ACME CORP"),
    ("ZETA", 1932393, "Zeta Imaging Inc."),
    ("ZETB", 1932394, "Zeta Two Inc."),
    ("OMGA", 77, "Omega Holdings Inc."),
]
ALIASES = {"acme": "ge", "zeta": "gehc", "zetb": "gehc", "omga": "ge"}
NAMES = {"acme": "ACME CORP", "zeta": "Zeta Imaging Inc.", "zetb": "Zeta Two Inc."}


@pytest.fixture
def no_real_http(monkeypatch):
    def refuse(self, request):
        raise AssertionError(f"real HTTP request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


def _need():
    return InformationNeed(
        node_id="n0.1",
        variable="Revenues",
        variable_aliases=["RevenueFromContractWithCustomerExcludingAssessedTax"],
        entity_hint=EntityHint(
            relation="disposed business",
            source_ticker="ACME",
            source_cik="0000000042",
            filing_date=DISPOSAL,
            filing_items=["1.01", "2.01", "9.01"],
            description="the business ACME disposed of",
        ),
        period=Period(start="2022-01-01", end="2022-12-31"),
        join_key=["cik", "concept", "period_start", "period_end"],
        test="add back",
        reason="test",
    )


def _doc(cik, name, form, day, text, items=()):
    return {
        "cik": cik,
        "name": f"{name}  (CIK {cik:010d})",
        "form": form,
        "file_date": day,
        "items": list(items),
        "text": text,
    }


def _form10(cik, name, n, day="2022-11-01"):
    text = "Separation and distribution agreement between Acme Corporation and the company"
    return [_doc(cik, name, "10-12B" if i else "10-12B/A", day, text) for i in range(n)]


def _scout(documents):
    fake = FakeZetaris(TICKERS, ALIASES, documents=documents, names=NAMES)
    tracer = RecordingTracer()
    d = ZetarisDiscovery(
        CATALOG, fake.client(tracer), tracer=tracer, user_agent=UA, sleep=lambda s: None
    )
    return source_scout.resolve(_need(), discovery=d, tracer=tracer), fake, tracer


def test_a_differently_named_spin_off_is_resolved_from_its_form_10(no_real_http):
    noise = [_doc(77, "Omega Holdings Inc.", "10-12G", "2022-06-01", "a customer, Acme Corp")]
    scout, fake, tracer = _scout(_form10(1932393, "Zeta Imaging Inc.", 12) + noise)
    assert scout.resolved and scout.entity.cik == "0001932393" and scout.entity.ticker == "zeta"
    best = next(c for c in scout.candidates if c.cik10 == "0001932393")
    assert best.qualifies and best.named == 12 and best.named_in == "registration"
    assert not best.brand  # resolved without sharing a word of the source's name
    assert "named in 12 document(s)" in scout.reason
    # one passing mention (below NAMED_SHARE of the top filer) is never a candidate
    assert "0000000077" not in {c.cik10 for c in scout.candidates}
    # the search went through Zetaris: a full-text REST table, registered once, UA masked
    fts = [n for n in fake.tables if n.startswith("fts_")]
    assert len(fts) == 1 and "q=%22acme%22" in fake.tables[fts[0]]
    assert "forms=10-12B,10-12G" in fake.tables[fts[0]]
    assert "startdt=2021-07-13&enddt=2023-01-04" in fake.tables[fts[0]]
    assert UA not in json.dumps(tracer.events)
    actions = {e["action"] for e in tracer.events if e["agent"] == "catalog"}
    assert {"register_fulltext", "fulltext", "tickers", "submissions"} <= actions
    line = next(e for e in tracer.events if e["agent"] == "source_scout")
    assert line["status"] == "success" and line["candidates"][0]["found_by"] == "registration"


def test_a_sale_to_an_existing_company_is_refused_with_the_reason(no_real_http):
    buyer = [
        _doc(77, "Omega Holdings Inc.", "8-K", "2023-01-04", "acquired from ACME Corp", items)
        for items in (("2.01", "9.01"), ("2.01", "9.01"))
    ]
    scout, fake, _ = _scout(buyer)
    assert not scout.resolved
    assert scout.reason.startswith("sale to an existing company: Omega Holdings Inc.")
    assert "cannot measure the disposed business" in scout.reason
    omega = next(c for c in scout.candidates if c.cik10 == "0000000077")
    assert omega.acquirer and not omega.qualifies and omega.annual_before > 0
    assert omega.named_in == "current report"
    # two searches: Form 10 registrations (none), then 8-Ks around the disposal
    searches = sorted(e for n, e in fake.tables.items() if n.startswith("fts_"))
    assert len(searches) == 2
    assert any(
        "forms=8-K&dateRange=custom&startdt=2022-12-28&enddt=2023-01-11" in e for e in searches
    )


def test_two_equal_counterparties_are_ambiguous_and_stay_at_the_frontier(no_real_http):
    docs = _form10(1932393, "Zeta Imaging Inc.", 6) + _form10(1932394, "Zeta Two Inc.", 6)
    scout, _, _ = _scout(docs)
    assert not scout.resolved and scout.reason.startswith("ambiguous: ZET")
    assert {c.cik10 for c in scout.candidates if c.qualifies} == {"0001932393", "0001932394"}


def test_no_evidence_at_all_says_what_was_searched(no_real_http):
    scout, _, _ = _scout([])
    assert not scout.resolved and "no candidate became a separate registrant" in scout.reason
    assert 'name searched: "acme"' in scout.reason


def test_sample_mode_without_a_cached_search_returns_nothing_and_says_so():
    tracer = RecordingTracer()
    d = SampleDiscovery(CATALOG, tracer=tracer)
    rows = d.filers_naming("acme", ["10-12B"], "2022-01-01", "2023-01-04")
    assert list(rows) == [] and "not in sample" in rows.note
    assert rows.sql.startswith("SELECT cik") and "FROM tiresias_sec.fts_" in rows.sql


def _hit(ciks, names, day, form, items=()):
    return {
        "_source": {
            "ciks": ciks,
            "display_names": names,
            "file_date": day,
            "form": form,
            "items": list(items),
        }
    }


SEARCH = {
    "hits": {
        "total": {"value": 5, "relation": "eq"},
        "hits": [
            _hit(["0000000007"], ["Seven Corp (CIK 0000000007)"], "2022-03-01", "10-12B"),
            _hit(["0000000007"], ["Seven Corp (CIK 0000000007)"], "2022-05-01", "10-12B/A"),
            _hit(["0000000009"], ["Nine Inc (CIK 0000000009)"], "2022-04-01", "10-12B"),
            # one document with two filers counts for each, under its own display name
            _hit(
                ["0000000008", "0000000009"],
                ["Eight LLC (CIK 0000000008)", "Nine Inc (CIK 0000000009)"],
                "2022-06-01",
                "8-K",
                ["2.01", "9.01"],
            ),
            _hit(["0000000008"], ["Eight LLC (CIK 0000000008)"], "2022-02-01", "8-K", ["2.01"]),
        ],
    }
}


def test_fulltext_hit_rows_aggregates_like_41_fulltext_hits_sql():
    rows = fulltext_hit_rows(SEARCH)
    # ORDER BY hits DESC, cik DESC
    assert [(r["cik"], r["hits"]) for r in rows] == [
        ("0000000009", 2),
        ("0000000008", 2),
        ("0000000007", 2),
    ]
    nine, eight, seven = rows
    assert nine["name"] == "Nine Inc (CIK 0000000009)" and nine["forms"] == "10-12B,8-K"
    assert eight["items"] == "2.01,9.01" and eight["first_date"] == "2022-02-01"
    assert seven["forms"] == "10-12B,10-12B/A" and seven["last_date"] == "2022-05-01"
    assert seven["items"] == "" and {r["total"] for r in rows} == {5}
    assert len(fulltext_hit_rows(SEARCH, limit=1)) == 1
    with pytest.raises(SourceError):
        fulltext_hit_rows({"hits": {"total": {"value": 1}, "hits": [{"no_source": 1}]}})


def test_sample_mode_serves_a_cached_search_under_the_live_table_name(tmp_path):
    endpoint = fulltext_endpoint(CATALOG, "acme", ["10-12B"], "2021-07-13", DISPOSAL)
    table = fulltext_table(endpoint)
    (tmp_path / fulltext_sample_file(table)).write_text(json.dumps(SEARCH))
    tracer = RecordingTracer()
    rows = SampleDiscovery(CATALOG, tmp_path, tracer=tracer).filers_naming(
        "ACME", ["10-12B", "10-12B/A"], "2021-07-13", DISPOSAL
    )
    assert [r.cik10 for r in rows] == ["0000000009", "0000000008", "0000000007"]
    assert rows[1].items == ("2.01", "9.01") and rows[0].total == 5
    assert f"FROM tiresias_sec.{table}" in rows.sql and rows.source == "sample"
    assert "5 matching document(s); 3 filer(s) shown" in rows.note
    line = next(e for e in tracer.events if e["action"] == "fulltext")
    assert line["rows"] == 3 and "served from data/sample/" in line["output_summary"]
    empty = {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}
    (tmp_path / fulltext_sample_file(table)).write_text(json.dumps(empty))
    none = SampleDiscovery(CATALOG, tmp_path).filers_naming(
        "acme", ["10-12B"], "2021-07-13", DISPOSAL
    )
    assert list(none) == [] and none.note.startswith("0 matching document(s)")


def test_every_cached_search_in_the_slice_is_named_after_its_provenance_url():
    root = Path(__file__).resolve().parents[3] / "data" / "sample"
    records = json.loads((root / "PROVENANCE.json").read_text())["files"]
    cached = sorted(p.name for p in root.glob("fts_*.json"))
    assert cached, "the slice caches at least one full-text search"
    by_file = {r["file"]: r for r in records}
    for name in cached:
        url = by_file[name]["source_url"]
        assert url.startswith("https://efts.sec.gov/LATEST/search-index?")
        assert fulltext_sample_file(fulltext_table(url)) == name


def test_search_phrase_uses_the_name_in_force_at_the_disposal():
    profile = Profile(
        cik10="0000000042",
        name="NEWNAME HOLDINGS CO",
        tickers=(),
        former_names=(
            {"name": "OLD ACME CORP /DE/", "from": "2001-01-01T00", "to": "2023-06-30T00"},
        ),
    )
    assert source_scout.search_phrase("X", profile, date(2023, 1, 4)) == "old acme"
    assert source_scout.search_phrase("X", profile, date(2024, 1, 4)) == "newname holdings"
    assert source_scout.search_phrase("Johnson & Johnson", None, date(2024, 1, 4)) == (
        "johnson & johnson"
    )
    assert source_scout.search_phrase("3M CO", None, date(2024, 1, 4)) == "3m"


def test_fulltext_endpoint_is_validated_and_encoded():
    url = fulltext_endpoint(CATALOG, "johnson & johnson", ["10-12B"], "2022-01-01", "2023-01-04")
    assert "q=%22johnson%20%26%20johnson%22" in url and '"' not in url and " " not in url
    roots = fulltext_endpoint(CATALOG, "acme", ["8-K", "8-K/A"], "2022-01-01", "2023-01-04")
    assert "forms=8-K&" in roots  # amendments match under the root form
    assert fulltext_table(url) == fulltext_table(url) and fulltext_table(url).startswith("fts_")
    for bad in (
        ('x"; DROP', ["10-12B"], "2022-01-01", "2023-01-04"),
        ("acme", ["10-12B; x"], "2022-01-01", "2023-01-04"),
        ("acme", ["10-12B"], "2022-1-1", "2023-01-04"),
        ("a", ["10-12B"], "2022-01-01", "2023-01-04"),
    ):
        with pytest.raises(ValueError):
            fulltext_endpoint(CATALOG, *bad)


def _filer(filings):
    return Profile(
        cik10="0000000005",
        name="New Co",
        tickers=(),
        former_names=(),
        filings=tuple(FilingRow(f, d, i, f"a-{n}") for n, (f, d, i) in enumerate(filings)),
    )


def test_one_annual_report_after_its_own_form_10_is_tolerated():
    row, day = TickerRow("NEWC", "0000000005", "New Co"), date(2030, 6, 1)
    near = ("8-K", "2030-06-01", "5.01,9.01")
    new = _filer([("10-12B", "2030-01-10", ""), ("10-K", "2030-03-01", ""), near])
    a = source_scout.assess(row, new, day)
    assert a.qualifies and "all after its own Form 10 filed 2030-01-10" in a.reason
    existing = _filer([("10-K", "2029-03-01", ""), ("10-12B", "2030-01-10", ""), near])
    assert not source_scout.assess(row, existing, day).qualifies
    earlier = _filer([("10-12B", "2028-06-01", ""), ("10-K", "2029-03-01", ""), near])
    assert not source_scout.assess(row, earlier, day).qualifies  # an earlier separation's filer
    two = _filer(
        [("10-12B", "2028-01-10", ""), ("10-K", "2029-03-01", ""), ("10-K", "2030-03-01", ""), near]
    )
    assert "already filed 2 annual report(s)" in source_scout.assess(row, two, day).reason
