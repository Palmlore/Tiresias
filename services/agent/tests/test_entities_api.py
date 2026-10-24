"""Entity directory: GET /entities (ready + searchable tiers, expected first-run wait) and
GET /entities/{cik}/suggestions (questions derived from the entity's own data, validated)."""

import json
import re

import pytest
from fake_zetaris import FakeZetaris
from fastapi.testclient import TestClient
from loop_helpers import EXAMPLES, RecordingTracer

from tiresias_agent import app as app_module
from tiresias_agent import entities
from tiresias_agent.adapters import zetaris as z
from tiresias_agent.adapters.catalog import make_discovery
from tiresias_agent.agents import suggester
from tiresias_agent.config import FIRST_RUN_SECONDS
from tiresias_agent.models import EntityList, RunRequest, SuggestionList
from tiresias_agent.orchestrator import run_investigation
from tiresias_agent.runs import RunRegistry

GE_CIK = "0000040545"
MMM_CIK = "0000066740"
UA = "Tiresias test test@example.com"
# Adjectives a suggested question must never carry (neutral wording).
LOADED = re.compile(
    r"\b(collapse\w*|plung\w*|soar\w*|surg\w*|crash\w*|skyrocket\w*|plummet\w*|tank\w*|"
    r"dramatic\w*|huge|massive|shock\w*|disast\w*|boom\w*)\b",
    re.IGNORECASE,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "registry", RunRegistry(max_runs=10, max_concurrent=4))
    return TestClient(app_module.app, raise_server_exceptions=False)


def _trace(tmp_path, run_id):
    return [json.loads(x) for x in (tmp_path / f"trace-{run_id}.jsonl").read_text().splitlines()]


# --- GET /entities, sample mode ----------------------------------------------------------------


def test_sample_entities_lists_the_slice_as_ready_and_says_why_nothing_else(client, tmp_path):
    r = client.get("/entities?sample_mode=true", headers={"X-Run-Id": "api-test-1"})
    assert r.status_code == 200 and r.headers["X-Run-Id"] == "api-test-1"
    body = EntityList.model_validate(r.json())
    assert body.mode == "sample" and body.searchable is False
    assert [e.ticker for e in body.entities] == ["GE", "GEHC", "GEV", "MMM", "SOLV"]
    lo, hi, basis = FIRST_RUN_SECONDS["sample"]
    for e in body.entities:
        assert e.tier == "ready" and set(e.metrics) == {"Revenues", "NetIncomeLoss"}
        assert (e.expected_first_run.min_seconds, e.expected_first_run.max_seconds) == (lo, hi)
        assert e.expected_first_run.basis == basis
    assert "offline slice" in body.note and "sample_mode=false" in body.note
    lines = _trace(tmp_path, "api-test-1")
    assert any(x["agent"] == "api" and x["action"] == "entities" for x in lines)


def test_sample_entities_type_ahead_filters_the_slice(client):
    body = client.get("/entities?q=vern&sample_mode=true").json()
    assert [e["ticker"] for e in body["entities"]] == ["GEV"]
    body = client.get("/entities?q=IBM&sample_mode=true").json()
    assert body["entities"] == [] and body["searchable"] is False


def test_entities_rejects_a_bad_limit(client):
    r = client.get("/entities?limit=0")
    assert r.status_code == 422 and r.json()["error"]["type"] == "ValidationError"


# --- GET /entities/{cik}/suggestions, sample mode ----------------------------------------------


def _key(req: RunRequest):
    compare = req.compare or {}
    restated = "filing_a" in compare and "filing_b" in compare
    return (
        req.metric,
        req.period.start.isoformat(),
        req.period.end.isoformat(),
        (compare.get("filing_a"), compare.get("filing_b")) if restated else "change",
    )


@pytest.mark.parametrize("cik", [GE_CIK, MMM_CIK])
def test_every_example_is_among_its_entitys_derived_suggestions(client, tmp_path, cik):
    r = client.get(f"/entities/{int(cik)}/suggestions?sample_mode=true")
    assert r.status_code == 200, r.text
    body = SuggestionList.model_validate(r.json())
    assert body.entity.cik == cik and body.entity.tier == "ready"
    assert body.considered > len(body.suggestions) >= 3
    got = {_key(s.request) for s in body.suggestions}
    mine = [
        p for p in EXAMPLES if json.loads(p.read_text())["entity"]["ticker"] == body.entity.ticker
    ]
    assert mine  # GE: examples 01-03; MMM: example 04
    for path in mine:
        example = RunRequest.model_validate(json.loads(path.read_text()))
        assert _key(example) in got, path.name
    kinds = {s.basis for s in body.suggestions}
    assert {"restatement", "largest change"} <= kinds


def test_every_suggestion_is_neutral_validated_and_runnable(client, tmp_path):
    body = SuggestionList.model_validate(
        client.get(f"/entities/{GE_CIK}/suggestions?sample_mode=true&role=auditor").json()
    )
    for s in body.suggestions:
        assert not LOADED.search(s.question), s.question
        assert re.search(r"\b(differ|rise|fall|was)\b", s.question), s.question
        assert s.request.question == s.question and s.request.asker.role == "auditor"
        assert abs(s.relative_change or 1) >= suggester.MIN_RELATIVE_CHANGE
        assert len(s.checks) == 4 and "framed as a" in s.checks[2]
        # Posted as-is, the run frames exactly the effect the suggestion promised.
        result = run_investigation(s.request, logs_dir=tmp_path)
        assert result.error is None and result.scope is None
        assert result.effect.kind == s.kind and result.effect.delta == s.delta


def test_suggestions_for_unknown_and_invalid_cik(client):
    r = client.get("/entities/12345/suggestions?sample_mode=true")
    assert r.status_code == 404 and r.json()["error"]["type"] == "EntityNotInSample"
    r = client.get("/entities/12a/suggestions")
    assert r.status_code == 422 and r.json()["error"]["type"] == "ValidationError"


class _TinySource:
    """One entity whose only restatement is 0.5% and whose only change is 0.2%."""

    name = "fake"

    def entities(self):
        return z.Evidence([z.Entity("acme", "0000000042", "ACME")], sql="-", source="fake")

    def concept_series(self, entity, concept):
        def fact(val, start, end, filed, accn):
            return z.Fact(concept, "USD", val, start, end, None, "FY", "10-K", filed, accn)

        facts = [
            fact(1000.0, "2022-01-01", "2022-12-31", "2023-02-01", "a1"),
            fact(1000.0, "2023-01-01", "2023-12-31", "2024-02-01", "a2"),
            fact(1002.0, "2022-01-01", "2022-12-31", "2024-02-01", "a2"),
            fact(1005.0, "2022-01-01", "2022-12-31", "2025-02-01", "a3"),
        ]
        return z.Evidence(facts if concept == "Revenues" else [], sql="-", source="fake")

    def filings(self, entity, forms=z.DEFAULT_FORMS):
        return z.Evidence([], sql="-", source="fake")


def test_trivial_changes_are_rejected_not_suggested():
    tracer = RecordingTracer()
    found = suggester.suggest(z.Entity("acme", "0000000042", "ACME"), _TinySource(), tracer=tracer)
    assert found.suggestions == [] and found.considered >= 1
    assert found.rejected == found.considered
    line = tracer.events[-1]
    assert line["agent"] == "suggester" and line["status"] == "rejected"
    assert "no annual values for NetIncomeLoss" in found.note


# --- live, with a fake Zetaris -----------------------------------------------------------------

TICKERS = [
    ("ACME", 42, "ACME CORP"),
    ("ACMX", 77, "ACME Industries Inc."),
    ("OTHR", 99, "Other Holdings Inc."),
]
# ACME is registered (answered from the GE sample); ACMX is only in sec_tickers.
REGISTERED = {"acme_facts_raw": "https://example.test/companyfacts/CIK0000000042.json"}
VIEWS = {"sec_tickers", "acme_filings", "acme_revenues", "acme_net_income"}


def _live(monkeypatch, **kw):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    fake = FakeZetaris(TICKERS, {"acme": "ge"}, tables=REGISTERED, views=VIEWS, **kw)
    tracer = RecordingTracer()
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    cache = {}

    def discovery():
        cache.setdefault("d", make_discovery(False, tracer, client=client))
        return cache["d"]

    return fake, tracer, source, discovery


def test_live_entities_ready_and_searchable_tiers_with_measured_waits(monkeypatch):
    fake, tracer, source, discovery = _live(monkeypatch)
    body = entities.list_entities(
        "acme", sample=False, source=source, discovery=discovery, tracer=tracer
    )
    assert body.mode == "live" and body.searchable is True and not body.degraded
    tiers = {(e.ticker, e.tier) for e in body.entities}
    assert tiers == {("ACME", "ready"), ("ACMX", "searchable")}
    ready = next(e for e in body.entities if e.tier == "ready")
    assert ready.name == "ACME CORP"  # named from sec_tickers (Zetaris tables carry only CIKs)
    assert set(ready.metrics) == {"Revenues", "NetIncomeLoss"}
    for e in body.entities:
        lo, hi, _ = FIRST_RUN_SECONDS[e.tier]
        assert (e.expected_first_run.min_seconds, e.expected_first_run.max_seconds) == (lo, hi)
    assert all("FROM tiresias.sec_tickers" in s for s in fake.sql if "sec_tickers" in s)
    api = [e for e in tracer.events if e["agent"] == "api"]
    assert api and api[-1]["action"] == "entities" and api[-1]["searchable"] is True


def test_live_entities_without_a_query_lists_ready_only_and_says_how_to_search(monkeypatch):
    _, tracer, source, discovery = _live(monkeypatch)
    body = entities.list_entities(
        None, sample=False, source=source, discovery=discovery, tracer=tracer
    )
    assert [e.ticker for e in body.entities] == ["ACME"] and body.searchable is False
    assert "?q=" in body.note


def test_live_suggestions_registered_registrable_and_unknown(monkeypatch):
    _, tracer, source, discovery = _live(monkeypatch)
    kw = {"sample": False, "source": source, "discovery": discovery, "tracer": tracer}
    body = entities.suggestions("42", **kw)
    assert body.mode == "live" and body.suggestions
    assert all(s.request.entity.ticker == "ACME" for s in body.suggestions)
    assert all(s.request.options.sample_mode is False for s in body.suggestions)
    with pytest.raises(entities.DirectoryError) as err:
        entities.suggestions("77", **kw)
    assert err.value.status == 409 and err.value.error_type == "EntityNotRegistered"
    assert "15-21 s" in str(err.value)
    with pytest.raises(entities.DirectoryError) as err:
        entities.suggestions("123456", **kw)
    assert err.value.status == 404 and err.value.error_type == "EntityNotFound"
    refused = [e for e in tracer.events if e["agent"] == "api" and e["status"] == "rejected"]
    assert {e["error_type"] for e in refused} == {"EntityNotRegistered", "EntityNotFound"}


def test_live_outage_degrades_and_never_crashes(monkeypatch):
    _, tracer, source, discovery = _live(monkeypatch, fail_all=True)
    body = entities.list_entities(
        "acme", sample=False, source=source, discovery=discovery, tracer=tracer
    )
    assert body.degraded is True
    assert "Zetaris unreachable" in (body.note or "")


def test_measured_wait_constants_match_the_measurement_doc():
    doc = (EXAMPLES[0].parents[1] / "docs" / "measurements" / "live-any-company.md").read_text()
    lo, hi, _ = FIRST_RUN_SECONDS["searchable"]
    assert f"{lo:.0f}–{hi:.0f} s end to end" in doc
    lo, hi, _ = FIRST_RUN_SECONDS["ready"]
    assert f"({lo:.0f}–{hi:.0f} s for a full investigation)" in doc
    assert "Measured **2026-10-23**" in doc


def test_live_partly_registered_entity_is_not_listed_as_ready(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    tables = {
        **REGISTERED,
        "acmx_facts_raw": "https://example.test/companyfacts/CIK0000000077.json",
    }
    fake = FakeZetaris(TICKERS, {"acme": "ge", "acmx": "gehc"}, tables=tables, views=VIEWS)
    tracer = RecordingTracer()
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    disc = make_discovery(False, tracer, client=client)
    kw = {"sample": False, "source": source, "discovery": lambda: disc, "tracer": tracer}
    assert [e.ticker for e in entities.list_entities(None, **kw).entities] == ["ACME"]
    hit = [e for e in entities.list_entities("acmx", **kw).entities if e.ticker == "ACMX"]
    assert [(e.tier, e.metrics) for e in hit] == [("searchable", [])]
    with pytest.raises(entities.DirectoryError) as err:
        entities.suggestions("77", **kw)
    assert err.value.status == 409 and "partly registered" in str(err.value)
