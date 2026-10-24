"""Any company: discovery only through Zetaris, the asked entity outside the showcase set
(sample and live), phase trace lines for a UI, and bounded registration."""

import json
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from fake_zetaris import FakeZetaris
from loop_helpers import ROOT, RecordingTracer, request

from tiresias_agent.adapters import zetaris as z
from tiresias_agent.adapters.catalog import (
    SampleDiscovery,
    ZetarisDiscovery,
    load_catalog,
    make_discovery,
    name_pattern,
)
from tiresias_agent.agents import entity_resolver
from tiresias_agent.config import LoopBudgets
from tiresias_agent.orchestrator import run_investigation, run_loop_a
from tiresias_agent.trace import Tracer, phase

SRC = ROOT / "services" / "agent" / "src" / "tiresias_agent"
CATALOG = load_catalog()
UA = "Tiresias test test@example.com"
# A company outside the showcase set, answered from the GE sample under another name.
ACME_TICKERS = [
    ("ACME", 42, "ACME CORP"),
    ("ACMH", 1932393, "ACME HealthCare Inc."),
    ("ACMV", 1996810, "ACME Vernova Inc."),
    ("ACMX", 77, "ACME Industries Inc."),
]
ACME_ALIASES = {"acme": "ge", "acmh": "gehc", "acmv": "gev"}


# --- no direct SEC HTTP in the agent ------------------------------------------------------------

NETWORK = re.compile(
    r"^\s*(import|from)\s+(httpx|requests|urllib\.request|urllib3|http\.client|aiohttp|socket)\b",
    re.MULTILINE,
)
# Each opens connections only to its sponsor service: Zetaris, Nightshift, the cognition service.
HTTP_ADAPTERS = {"zetaris.py", "nightshift.py", "cognition.py"}


def test_only_the_sponsor_adapters_import_an_http_client_and_no_code_names_sec():
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name not in HTTP_ADAPTERS:
            assert not NETWORK.search(text), f"{path.name} imports an HTTP client"
        # SEC endpoints live only in catalog/sources.yaml and zetaris/sql/ (Zetaris fetches them).
        assert "sec.gov" not in text, f"{path.name} names an SEC endpoint"
    catalog_py = (SRC / "adapters" / "catalog.py").read_text(encoding="utf-8")
    assert "httpx" not in catalog_py and "LiveDiscovery" not in catalog_py


@pytest.fixture
def no_real_http(monkeypatch):
    """Any HTTP request that does not go through a MockTransport fails the test."""

    def refuse(self, request):
        raise AssertionError(f"real HTTP request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


def test_make_discovery_live_is_zetaris_backed(no_real_http):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    d = make_discovery(False, client=fake.client())
    assert isinstance(d.primary, ZetarisDiscovery) and isinstance(d.fallback, SampleDiscovery)


# --- live discovery through Zetaris --------------------------------------------------------------


def test_zetaris_discovery_registers_tickers_once_and_submissions_on_demand(no_real_http):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    tracer = RecordingTracer()
    d = ZetarisDiscovery(
        CATALOG, fake.client(tracer), tracer=tracer, user_agent=UA, sleep=lambda s: None
    )
    rows = d.lookup(tickers=["acme"])
    assert [(r.ticker, r.cik10) for r in rows] == [("ACME", "0000000042")]
    assert "FROM tiresias.sec_tickers" in rows.sql and rows.source == "zetaris"
    assert {"sec_tickers"} <= fake.views and "sec_tickers_raw" in fake.tables
    brand = d.brand(["acme"])
    assert [r.cik10 for r in brand][:2] == ["0001996810", "0001932393"]  # newest CIK first
    p = d.submissions("0001932393", "ACMH")
    assert p[0].name.startswith("GE HealthCare") and p[0].filings
    assert fake.tables["acmh_submissions_raw"].endswith("CIK0001932393.json")
    created = [s for s in fake.sql if s.startswith("CREATE LIGHTNING REST TABLE")]
    d.submissions("0001932393", "ACMH")  # second read: no new registration
    assert [s for s in fake.sql if s.startswith("CREATE LIGHTNING REST TABLE")] == created
    assert len([s for s in fake.sql if "sec_tickers_raw FROM" in s]) == 1
    assert UA not in json.dumps(tracer.events)  # the user agent is masked in traces
    lines = [e for e in tracer.events if e["agent"] == "catalog"]
    assert {e["action"] for e in lines} >= {"register_tickers", "register_submissions"}


def test_zetaris_discovery_refuses_a_submissions_table_bound_to_another_cik(no_real_http):
    tables = {"acmh_submissions_raw": "https://example.test/submissions/CIK0000000099.json"}
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES, tables=tables, views={"sec_tickers"})
    d = ZetarisDiscovery(CATALOG, fake.client(), user_agent=UA, sleep=lambda s: None)
    with pytest.raises(z.SourceError) as err:
        d.submissions("0001932393", "ACMH")
    assert not err.value.recoverable and "bound to CIK 0000000099" in str(err.value)


def test_name_pattern_is_validated():
    assert name_pattern("Macy's  Inc") == "%macy_s inc%"
    for bad in ("100%", "x; DROP TABLE y", ""):
        with pytest.raises(ValueError):
            name_pattern(bad)


# --- the asked entity: sample mode ---------------------------------------------------------------


def test_sample_mode_unknown_company_ends_fast_with_a_clear_reason(tmp_path):
    req = request("01", entity={"ticker": "IBM"})
    req.options.sample_mode = True
    start = time.perf_counter()
    result = run_investigation(req, logs_dir=tmp_path)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    assert result.end_state == "evidence-frontier" and result.error is None
    reason = result.information_need.reason
    assert "not in the offline sample" in reason
    assert all(t in reason for t in ("GE (", "GEHC (", "GEV ("))
    assert "sample_mode=false" in reason and "--live" in reason
    assert reason in result.root_cause
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    resolve = next(x for x in lines if x["agent"] == "entity_resolver")
    assert resolve["status"] == "rejected" and resolve["error_type"] == "EntityNotInSample"
    assert not any(x["agent"] in {"onboarder", "source_scout", "analyst"} for x in lines)


def test_sample_mode_resolves_a_showcase_entity_by_name(tmp_path):
    req = request("03", entity={"name": "general electric"})
    req.options.sample_mode = True
    result = run_loop_a(req, logs_dir=tmp_path)
    assert result.end_state == "actionable" and result.effect.entity.lower() == "ge"


# --- the asked entity: live, with a fake Zetaris --------------------------------------------------


def _live(fake, prefix, tmp_path, entity, monkeypatch, **options):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    req = request(prefix, entity=entity)
    req.options.sample_mode = False
    for k, v in options.items():
        setattr(req.options, k, v)
    tracer = Tracer("run-any-company-test", logs_dir=tmp_path, defaults={"mode": "live"})
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    return result, lines


def test_live_unknown_company_is_resolved_registered_and_investigated(
    tmp_path, monkeypatch, no_real_http
):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    result, lines = _live(fake, "01", tmp_path, {"ticker": "ACME"}, monkeypatch)
    assert result.error is None and result.end_state == "actionable"
    # The asked entity and the disposed business: two evidence sources, both gated.
    assert result.stats.sources_onboarded == 2
    assert {"acme_facts_raw", "acme_submissions_raw", "acmh_facts_raw"} <= set(fake.tables)
    assert {"acme_filings", "acme_revenues", "acme_income_cont_ops"} <= fake.views
    gates = [x for x in lines if x["agent"] == "gate" and x.get("gate") == "source"]
    assert len(gates) == 2
    regs = [x for x in lines if x["agent"] == "onboarder" and x["action"] == "register"]
    assert regs[0]["scope"] == "asked_entity" and regs[0]["outcome"] == "registered"
    # Discovery tables (tickers, candidates' submissions) are not counted as sources.
    assert "acmh_submissions_raw" in fake.tables and "sec_tickers_raw" in fake.tables
    root = result.why_tree[0]
    assert any("sec_tickers" in ev.sql for ev in root.evidence)
    # Discovery and evidence SQL all went to Zetaris; nothing fetched SEC directly.
    assert any("FROM tiresias.sec_tickers" in s for s in fake.sql)
    assert all(x.get("source") != "sec" for x in lines)
    # B-9: re-registering the database and container ("already exists") is no red line.
    zetaris_lines = [x for x in lines if x["agent"] == z.AGENT]
    assert not [x for x in zetaris_lines if x["status"] == "error"]
    assert {"bootstrap:database", "bootstrap:container"} <= {
        x["action"] for x in zetaris_lines if x.get("outcome") == "exists"
    }


def test_live_second_run_reuses_the_registration(tmp_path, monkeypatch, no_real_http):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    _live(fake, "03", tmp_path, {"ticker": "ACME"}, monkeypatch)
    before = len(fake.tables)
    result, lines = _live(fake, "03", tmp_path / "warm", {"ticker": "ACME"}, monkeypatch)
    assert result.end_state == "actionable" and result.stats.sources_onboarded == 0
    assert len(fake.tables) == before
    assert not any(x["agent"] == "onboarder" for x in lines)


@pytest.mark.parametrize(
    ("entity", "error_type", "words"),
    [
        ({"ticker": "NOPE"}, "EntityNotFound", "no SEC registrant"),
        ({"name": "acme"}, "EntityAmbiguous", "matches 4 registrants"),
        ({"ticker": "ACME", "cik": "77"}, "EntityAmbiguous", "disagree"),
    ],
)
def test_live_unresolvable_or_ambiguous_company_is_a_frontier(
    tmp_path, monkeypatch, no_real_http, entity, error_type, words
):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    result, lines = _live(fake, "03", tmp_path, entity, monkeypatch)
    assert result.error is None and result.end_state == "evidence-frontier"
    assert words in result.information_need.reason
    resolve = next(x for x in lines if x["agent"] == "entity_resolver")
    assert resolve["error_type"] == error_type
    assert not [s for s in fake.sql if s.startswith("CREATE LIGHTNING REST TABLE acme")]


def test_live_name_resolves_exact_title_and_reuses_it_when_warm(
    tmp_path, monkeypatch, no_real_http
):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    result, _ = _live(fake, "03", tmp_path, {"name": "ACME Corp"}, monkeypatch)
    assert result.end_state == "actionable" and result.stats.sources_onboarded == 1
    result, lines = _live(fake, "03", tmp_path / "warm", {"name": "ACME Corp"}, monkeypatch)
    assert result.end_state == "actionable" and result.stats.sources_onboarded == 0
    assert not any(x["agent"] == "onboarder" for x in lines)


def test_live_source_gate_deny_and_cap_zero_register_nothing(tmp_path, monkeypatch, no_real_http):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    result, _ = _live(
        fake,
        "03",
        tmp_path,
        {"ticker": "ACME"},
        monkeypatch,
        hitl_mode="scripted",
        decisions={"source": "deny"},
    )
    assert result.end_state == "evidence-frontier" and "denied" in result.information_need.reason
    monkeypatch.setenv("MAX_NEW_SOURCES_PER_RUN", "0")
    result, _ = _live(fake, "03", tmp_path / "cap", {"ticker": "ACME"}, monkeypatch)
    assert "MAX_NEW_SOURCES_PER_RUN=0" in result.information_need.reason
    assert not [t for t in fake.tables if t.startswith("acme_")]


def test_live_outage_never_crashes_and_says_why(tmp_path, monkeypatch, no_real_http):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES, fail_all=True)
    result, lines = _live(fake, "03", tmp_path, {"ticker": "ACME"}, monkeypatch)
    assert result.error is None and result.end_state == "evidence-frontier"
    assert "Zetaris unreachable" in result.information_need.reason
    assert any(x["status"] == "degraded" for x in lines)


def test_registration_stops_at_the_run_budget():
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    report = z.bootstrap(
        fake.client(),
        sec_user_agent=UA,
        entities=[z.Entity("acme", "0000000042", "ACME CORP")],
        deadline=10.0,
        clock=lambda: 10.5,
    )
    assert report and all(e["status"] == "skipped" and e["budget"] for e in report)
    assert fake.sql == []


def test_bootstrap_plan_adds_the_discovery_objects_only_on_request():
    plain = [s for s, _, _, _ in z.bootstrap_plan(UA)]
    full = [s for s, _, _, _ in z.bootstrap_plan(UA, discovery=True)]
    assert "tickers_table" not in plain
    assert full[:5] == ["database", "container", "tickers_table", "cache", "tickers_view"]


# --- phase lines for a UI -----------------------------------------------------------------------


def test_sample_run_brackets_every_slow_phase(tmp_path):
    req = request("01")
    req.options.sample_mode = True
    result = run_investigation(req, logs_dir=tmp_path)
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    starts = [x["phase"] for x in lines if x["action"] == "phase_start"]
    dones = [x for x in lines if x["action"] == "phase_done"]
    assert starts == [
        "resolve_entity",
        "scope_check",
        "loop_a",
        "frame",
        "loop_b",
        "scout",
        "onboard",
        "reconcile",
    ]
    assert sorted(starts) == sorted(x["phase"] for x in dones)
    for x in [*dones, *[y for y in lines if y["action"] == "phase_start"]]:
        assert x["agent"] == "orchestrator" and x["target_agent"]
    assert all(isinstance(x["duration_ms"], int) for x in dones)
    # Each phase closes after it opens, nested phases close before their parent.
    order = [(x["action"], x["phase"]) for x in lines if x["action"].startswith("phase_")]
    stack = []
    for action, name in order:
        if action == "phase_start":
            stack.append(name)
        else:
            assert stack.pop() == name


def test_phase_heartbeats_while_waiting_and_marks_errors():
    tracer = RecordingTracer()
    with phase(tracer, "register_entity", owner="onboarder", every=0.05):
        time.sleep(0.23)
    beats = [e for e in tracer.events if e["action"] == "heartbeat"]
    assert len(beats) >= 3 and all(e["phase"] == "register_entity" for e in beats)
    assert beats[-1]["elapsed_ms"] >= 150
    assert tracer.events[-1]["action"] == "phase_done" and tracer.events[-1]["status"] == "success"
    with pytest.raises(RuntimeError), phase(tracer, "frame", owner="framer", every=10):
        raise RuntimeError("boom")
    assert tracer.events[-1]["status"] == "error" and tracer.events[-1]["phase"] == "frame"
    time.sleep(0.1)
    assert not [t for t in threading.enumerate() if t.name.startswith("heartbeat-")]


def test_entity_resolver_reports_the_sql_it_ran():
    tracer = RecordingTracer()
    req = request("01", entity={"ticker": "IBM"})
    res = entity_resolver.resolve(req, source=z.SampleSource(), sample=True, tracer=tracer)
    assert not res.ok and res.error_type == "EntityNotInSample"
    line = tracer.events[-1]
    assert line["agent"] == "entity_resolver" and "SHOW LIGHTNING TABLES" in line["sql"]


def test_loop_budget_default_is_unchanged():
    assert LoopBudgets.from_env({}).max_new_sources == 2
