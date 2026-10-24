"""Zetaris resilience (BACKLOG B-24): a dead engine is an outage, a liveness probe before a live
run, a bounded companyfacts cache (ZETARIS_MAX_CACHED_FACTS) and released discovery tables.

No network: every Zetaris reply comes from httpx.MockTransport (tests/fake_zetaris.py).
"""

import json
from pathlib import Path

import httpx
import pytest
import yaml
from fake_zetaris import FakeZetaris
from fastapi.testclient import TestClient
from loop_helpers import request

from tiresias_agent import app as app_module
from tiresias_agent.adapters import catalog as cat
from tiresias_agent.adapters import zetaris as z
from tiresias_agent.config import LivenessSettings, Settings, WarmSettings, max_cached_facts
from tiresias_agent.orchestrator import run_investigation
from tiresias_agent.trace import Tracer

KEY = "zk-resilience-secret-0123456789"
UA = "Tiresias test test@example.com"
CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "agent.yaml"
STOPPED = (
    "org.apache.hive.service.cli.HiveSQLException: java.lang.IllegalStateException: "
    "Cannot call methods on a stopped SparkContext.\nThis stopped SparkContext was created at: ..."
)
NPE = (
    'java.lang.NullPointerException: Cannot invoke "Object.hashCode()"\n'
    "\tat com.zetaris.lightning.FetchIterator.fetchNext(FetchIterator.scala:88)"
)
ACME_TICKERS = [
    ("ACME", 42, "ACME CORP"),
    ("ACMH", 1932393, "ACME HealthCare Inc."),
    ("ACMV", 1996810, "ACME Vernova Inc."),
    ("ACMX", 77, "ACME Industries Inc."),
]
ACME_ALIASES = {"acme": "ge", "acmh": "gehc", "acmv": "gev"}


@pytest.fixture(autouse=True)
def _no_ambient_settings(monkeypatch):
    for name in (
        "ZETARIS_MAX_CACHED_FACTS",
        "ZETARIS_LIVENESS_TTL_SECONDS",
        "ZETARIS_LIVENESS_TIMEOUT_SECONDS",
        "ZETARIS_WARM_MAX_TABLES",
        "ZETARIS_WARM_ENTITIES",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_real_http(monkeypatch):
    def refuse(self, request):
        raise AssertionError(f"real HTTP request to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


class RecordingTracer:
    run_id = "run-resilience-1"

    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def make_client(handler, *, tracer=None, max_retries=2):
    config = z.ZetarisConfig(
        rest_url="http://zetaris.test/api/v1.0",
        api_key=KEY,
        timeout_seconds=5.0,
        max_retries=max_retries,
    )
    return z.ZetarisClient(
        config, tracer=tracer, transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def ok(headers=(), rows=()):
    return httpx.Response(200, json={"headers": list(headers), "data": list(rows)})


def sql_of(request):
    return " ".join(json.loads(request.content)["sql"].split())


# --- Fix A1: a dead engine is an outage ----------------------------------------------------------


@pytest.mark.parametrize("body", [STOPPED, NPE, "x" * 900 + STOPPED])
def test_dead_engine_400_is_recoverable_and_degrades_to_the_sample(body):
    tracer, sent = RecordingTracer(), []

    def handler(request):
        sent.append(sql_of(request))
        return httpx.Response(400, json={"msg": body})

    client = make_client(handler, tracer=tracer)
    source = z.make_source(False, tracer, client=client)
    facts = source.concept_series("ge", "Revenues")
    assert facts and facts.source == "sample" and facts.degraded
    assert "SparkContext stopped" in facts.error and "restart" in facts.error
    degraded = [e for e in tracer.events if e["status"] == "degraded"]
    assert len(degraded) == 1 and degraded[0]["source"] == "sample"
    error = next(e for e in tracer.events if e["status"] == "error")
    assert error["outcome"] == "engine_down" and error["retry_count"] == 0
    assert len(sent) == 1  # a dead engine is not retried


def test_dead_engine_is_classified_in_one_place():
    assert z.dead_engine(STOPPED) and z.dead_engine(NPE.upper())
    assert not z.dead_engine("java.lang.NullPointerException at Foo.bar")
    assert not z.dead_engine("[PARSE_SYNTAX_ERROR] Syntax error at or near 'SELEC'")
    assert all(all(p == p.lower() for p in sig) for sig in z.DEAD_ENGINE_SIGNATURES)


@pytest.mark.parametrize(
    "body",
    [
        "[PARSE_SYNTAX_ERROR] Syntax error at or near 'SELEC'",
        "Unauthorized: invalid token",
        "java.lang.NullPointerException at Foo.bar",
    ],
)
def test_genuine_4xx_stays_non_recoverable_and_is_raised(body):
    tracer = RecordingTracer()
    client = make_client(lambda r: httpx.Response(400, json={"msg": body}), tracer=tracer)
    source = z.make_source(False, tracer, client=client)
    with pytest.raises(z.SourceError) as excinfo:
        source.concept_series("ge", "Revenues")
    assert not excinfo.value.recoverable and not excinfo.value.dead_engine
    assert not [e for e in tracer.events if e["status"] == "degraded"]


def test_dead_engine_degrades_discovery_to_the_sample():
    tracer = RecordingTracer()
    client = make_client(lambda r: httpx.Response(400, json={"msg": STOPPED}), tracer=tracer)
    discovery = cat.make_discovery(False, tracer, client=client)
    rows = discovery.lookup(tickers=["GE"])
    assert rows and rows.degraded and rows.source == "sample"
    assert any(e["status"] == "degraded" for e in tracer.events)


# --- Fix A2: liveness probe ---------------------------------------------------------------------


def test_liveness_probe_short_circuits_a_dead_engine_once_per_ttl():
    sent, tracer = [], RecordingTracer()

    def handler(request):
        sent.append(sql_of(request))
        return httpx.Response(400, json={"msg": STOPPED})

    probe, now = z.LivenessProbe(), [100.0]
    settings = LivenessSettings(ttl_seconds=30, timeout_seconds=3)
    first = make_client(handler, tracer=tracer)
    r1 = probe.check(first, settings=settings, clock=lambda: now[0])
    assert r1.down and r1.dead_engine and sent == ["SELECT 1 AS alive"]
    second = make_client(handler, tracer=tracer)
    now[0] += 10
    r2 = probe.check(second, settings=settings, clock=lambda: now[0])
    assert r2 is r1 and len(sent) == 1  # reused within the TTL
    now[0] += 25
    probe.check(second, settings=settings, clock=lambda: now[0])
    assert len(sent) == 2  # probed again after it
    lines = [e for e in tracer.events if e["action"] == "liveness"]
    assert [x["cached"] for x in lines] == [False, True, False]
    assert {x["status"] for x in lines} == {"degraded"} and lines[0]["outcome"] == "down"
    assert probe.snapshot()["state"] == "down"


def test_a_down_client_sends_nothing_and_every_read_is_served_from_the_sample():
    sent, tracer = [], RecordingTracer()

    def handler(request):
        sent.append(sql_of(request))
        raise httpx.ConnectError("refused")

    client = make_client(handler, tracer=tracer)
    result = z.check_liveness(client, tracer=tracer, probe=z.LivenessProbe())
    assert result.down and "unreachable" in result.reason
    assert sent == ["SELECT 1 AS alive"]  # no retry for the probe
    facts = z.make_source(False, tracer, client=client).concept_series("ge", "Revenues")
    assert facts.degraded and facts.source == "sample" and "not sent" in facts.error
    assert sent == ["SELECT 1 AS alive"]


def test_liveness_inconclusive_lets_the_run_go_ahead_live():
    client = make_client(lambda r: httpx.Response(401, json={"msg": "Unauthorized"}))
    result = z.check_liveness(client, probe=z.LivenessProbe())
    assert result.state == "inconclusive" and client.down_reason is None


def test_live_run_against_a_dead_engine_probes_once_and_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    sent = []

    def handler(request):
        sent.append(sql_of(request))
        return httpx.Response(400, json={"msg": STOPPED})

    def run(run_id):
        req = request("03")
        req.options.sample_mode = False
        tracer = Tracer(run_id, logs_dir=tmp_path, defaults={"mode": "live"})
        client = make_client(handler, tracer=tracer)
        source = z.make_source(False, tracer, client=client)
        result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
        return result, [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]

    result, lines = run("run-dead-1")
    assert result.error is None and result.end_state == "actionable"  # GE is in the sample
    assert sent == ["SELECT 1 AS alive"]
    probe = [x for x in lines if x["action"] == "liveness"]
    assert len(probe) == 1 and probe[0]["outcome"] == "down" and not probe[0]["cached"]
    assert any(x["status"] == "degraded" and x["source"] == "sample" for x in lines)
    result, lines = run("run-dead-2")  # a second run within the TTL reuses the verdict
    assert result.error is None and sent == ["SELECT 1 AS alive"]
    assert [x["cached"] for x in lines if x["action"] == "liveness"] == [True]


def test_health_reports_zetaris_liveness_and_cache_per_the_contract(monkeypatch):
    schemas = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    spec = schemas["ZetarisHealth"]
    body = TestClient(app_module.app).get("/health").json()["zetaris"]
    assert body["state"] in spec["properties"]["state"]["enum"]
    assert set(spec["required"]) <= set(body) <= set(spec["properties"])
    monkeypatch.setattr(app_module, "settings", Settings(sample_mode=False))
    z.FACTS_CACHE.add("ge_facts_raw")
    body = app_module.zetaris_health()
    assert body["state"] == "not-checked" and body["cached_facts"] == ["ge_facts_raw"]
    assert body["max_cached_facts"] == 5 and set(body) <= set(spec["properties"])


# --- Fix B3: the companyfacts cache cap -----------------------------------------------------------


def recording(fail_uncache=False):
    sent = []

    def handler(request):
        s = sql_of(request)
        sent.append(s)
        if fail_uncache and s.startswith("UNCACHE"):
            return httpx.Response(503, text="busy")
        if s.startswith(("CREATE LIGHTNING DATABASE", "CREATE SCHEMASTORE CONTAINER")):
            return httpx.Response(400, json={"msg": "An object named X already exists."})
        return ok()

    return handler, sent


def entity(t, cik):
    return z.Entity(t, f"{cik:010d}", t.upper())


def register(client, t, cik, **kw):
    return z.bootstrap(
        client,
        sec_user_agent=UA,
        entities=[entity(t, cik)],
        concepts=["Revenues"],
        tables=("facts",),
        **kw,
    )


def uncached(sent):
    return [s.removeprefix("UNCACHE TABLE tiresias_sec.") for s in sent if s.startswith("UNCACHE")]


def test_cap_uncaches_the_least_recently_used_table(monkeypatch):
    monkeypatch.setenv("ZETARIS_MAX_CACHED_FACTS", "2")
    handler, sent = recording()
    tracer = RecordingTracer()
    client, cache = make_client(handler, tracer=tracer), z.FactsCache()
    register(client, "aaa", 1, cache=cache)
    register(client, "bbb", 2, cache=cache)
    cache.touch("aaa_facts_raw")  # aaa queried since: bbb is now least recently used
    register(client, "ccc", 3, cache=cache)
    assert uncached(sent) == ["bbb_facts_raw"]
    assert cache.tables() == ["aaa_facts_raw", "ccc_facts_raw"]
    line = next(e for e in tracer.events if e["action"] == "uncache")
    assert line["status"] == "success" and line["table"] == "TIRESIAS_SEC.bbb_facts_raw"
    assert "ZETARIS_MAX_CACHED_FACTS=2" in line["reason"] and "ccc_facts_raw" in line["reason"]
    assert not [s for s in sent if s.startswith(("DROP", "DELETE"))]


def test_cap_never_evicts_the_asked_entity_or_the_counterparty(monkeypatch):
    monkeypatch.setenv("ZETARIS_MAX_CACHED_FACTS", "2")
    handler, sent = recording()
    client, cache = make_client(handler), z.FactsCache()
    register(client, "asked", 1, cache=cache)
    register(client, "party", 2, cache=cache)
    cache.pin("party_facts_raw")  # a running run's Loop B counterparty
    report = register(client, "newco", 3, cache=cache, protect=["asked"])
    assert uncached(sent) == []
    cache_entry = next(e for e in report if e["step"] == "cache")
    assert cache_entry["status"] == "cached" and cache_entry["cache_cap"] == "exceeded"
    assert max_cached_facts() == 2


def test_live_run_never_uncaches_the_asked_entity_or_the_counterparty(
    tmp_path, monkeypatch, no_real_http
):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setenv("ZETARIS_MAX_CACHED_FACTS", "2")
    z.FACTS_CACHE.add("old1_facts_raw")  # cached by earlier runs, least recently used first
    z.FACTS_CACHE.add("old2_facts_raw")
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    req = request("01", entity={"ticker": "ACME"})
    req.options.sample_mode = False
    tracer = Tracer("run-cap-test", logs_dir=tmp_path, defaults={"mode": "live"})
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    assert result.error is None and result.end_state == "actionable"
    gone = uncached(fake.sql)
    assert {"old1_facts_raw", "old2_facts_raw"} <= set(gone)  # one for acme, one for acmh
    assert not {"acme_facts_raw", "acmh_facts_raw", "acme_submissions_raw"} & set(gone)
    assert z.FACTS_CACHE.tables() == ["acme_facts_raw", "acmh_facts_raw"]
    assert z.FACTS_CACHE.pinned() == set()  # unpinned when the run ended


def test_uncache_failure_never_fails_the_registration_or_the_run(
    tmp_path, monkeypatch, no_real_http
):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    monkeypatch.setenv("ZETARIS_MAX_CACHED_FACTS", "1")
    z.FACTS_CACHE.add("old1_facts_raw")
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)

    def flaky(request):
        if sql_of(request).startswith("UNCACHE"):
            fake.sql.append(sql_of(request))
            return httpx.Response(500, text="uncache exploded")
        return fake(request)

    req = request("01", entity={"ticker": "ACME"})
    req.options.sample_mode = False
    tracer = Tracer("run-uncache-fail", logs_dir=tmp_path, defaults={"mode": "live"})
    cfg = z.ZetarisConfig(rest_url="http://zetaris.test/api/v1.0", api_key="k", max_retries=0)
    client = z.ZetarisClient(cfg, tracer=tracer, transport=httpx.MockTransport(flaky))
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    assert result.error is None and result.end_state == "actionable"
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    failed = [x for x in lines if x["action"] == "uncache" and x["status"] == "error"]
    assert failed and all(x.get("reason") and x.get("table") for x in failed)
    assert "old1_facts_raw" in z.FACTS_CACHE.tables()  # still counted: it may still be cached


def test_warm_respects_the_cache_cap(monkeypatch):
    monkeypatch.setenv("ZETARIS_MAX_CACHED_FACTS", "2")
    tables = {
        "ge_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000040545.json",
        "gev_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001996810.json",
        "ibm_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000051143.json",
        "ge_submissions_raw": "https://data.sec.gov/submissions/CIK0000040545.json",
    }
    sent = []

    def handler(request):
        s = sql_of(request)
        sent.append(s)
        if s.startswith("SHOW LIGHTNING TABLES"):
            return ok(["name", "source_table"], [[n, e] for n, e in tables.items()])
        return ok()

    cache = z.FactsCache()
    cache.add("old_facts_raw")  # cached by an earlier run: makes room for warm's first table
    settings = WarmSettings(entities=("ge", "gev"), max_tables=11)
    report = z.warm(make_client(handler), settings=settings, cache=cache)
    status = {e["table"].split(".")[-1]: e for e in report}
    assert status["ge_facts_raw"]["status"] == "warmed"
    assert status["gev_facts_raw"]["status"] == "warmed"
    assert status["ibm_facts_raw"]["status"] == "capped"
    assert status["ibm_facts_raw"]["reason"] == "cache_cap"
    assert status["ge_submissions_raw"]["status"] == "warmed"  # only companyfacts count
    assert uncached(sent) == ["old_facts_raw"]
    assert not any("CACHE TABLE tiresias_sec.ibm_facts_raw" == s for s in sent)
    # The first warm_order entity is the last to be evicted.
    assert cache.tables() == ["gev_facts_raw", "ge_facts_raw"]


# --- Fix B4: discovery tables are released after the scout ---------------------------------------


def test_scout_release_uncaches_unchosen_candidates_and_fulltext_tables(no_real_http):
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES, views={"sec_tickers"})
    tracer = RecordingTracer()
    d = cat.ZetarisDiscovery(
        cat.load_catalog(), fake.client(tracer), tracer=tracer, user_agent=UA, sleep=lambda s: 0
    )
    d.submissions("0001932393", "ACMH")
    d.submissions("0001996810", "ACMV")
    d.submissions("0000000042", "ACME")
    d.filers_naming("acme healthcare", ["10-12B"], "2022-01-01", "2023-01-31")
    fts = next(t for t in d.cached if t.startswith("fts_"))
    report = d.release_cache(keep=["ACME", "ACMH"])  # the asked entity and the chosen one
    gone = uncached(fake.sql)
    assert sorted(gone) == sorted(["acmv_submissions_raw", fts])
    assert {r["table"] for r in report} == set(gone) and all(r["released"] for r in report)
    reasons = {e["table"]: e["reason"] for e in tracer.events if e["action"] == "uncache"}
    assert "not chosen" in reasons["TIRESIAS_SEC.acmv_submissions_raw"]
    assert "full-text" in reasons[f"TIRESIAS_SEC.{fts}"]
    assert not [s for s in fake.sql if s.startswith(("DROP", "DELETE"))]
    assert "acmv_submissions_raw" in fake.tables  # still registered
    # A later scout reading the released table re-caches it first.
    again = cat.ZetarisDiscovery(
        cat.load_catalog(), fake.client(tracer), tracer=tracer, user_agent=UA, sleep=lambda s: 0
    )
    before = len(fake.sql)
    again.submissions("0001996810", "ACMV")
    assert "CACHE TABLE tiresias_sec.acmv_submissions_raw" in fake.sql[before:]
    assert again.cached == ["acmv_submissions_raw"]


def test_live_run_releases_discovery_tables_after_the_scout(tmp_path, monkeypatch, no_real_http):
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    fake = FakeZetaris(ACME_TICKERS, ACME_ALIASES)
    req = request("01", entity={"ticker": "ACME"})
    req.options.sample_mode = False
    tracer = Tracer("run-release-test", logs_dir=tmp_path, defaults={"mode": "live"})
    client = fake.client(tracer)
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    assert result.error is None and result.end_state == "actionable"
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    scout_done = next(
        i for i, x in enumerate(lines) if x["action"] == "phase_done" and x["phase"] == "scout"
    )
    releases = [(i, x) for i, x in enumerate(lines) if x["action"] == "uncache"]
    assert releases and all(x["status"] == "success" and x["reason"] for _, x in releases)
    released = {x["table"] for _, x in releases}
    assert "TIRESIAS_SEC.acme_submissions_raw" not in released  # the asked entity's filings
    assert all(i > scout_done for i, _ in releases)
    assert not [x for x in lines if x["agent"] == z.AGENT and x["status"] == "error"]


# --- sample mode ---------------------------------------------------------------------------------


def test_sample_mode_sends_no_probe_and_no_uncache(tmp_path, no_real_http):
    req = request("01")
    req.options.sample_mode = True
    result = run_investigation(req, logs_dir=tmp_path)
    assert result.error is None
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    assert not [x for x in lines if x["action"] in {"liveness", "uncache", "liveness:probe"}]
    assert z.FACTS_CACHE.tables() == [] and z.LIVENESS.snapshot() == {"state": "not-checked"}


def test_uncache_template_is_one_validated_statement_and_never_drops():
    assert z.uncache_sql("ge_facts_raw") == "UNCACHE TABLE tiresias_sec.ge_facts_raw"
    assert z.load_template("42_liveness.sql") == "SELECT 1 AS alive"
    for bad in ("ge_facts_raw; DROP TABLE x", "GE-facts", "", "../x"):
        with pytest.raises(ValueError):
            z.uncache_sql(bad)
    tracer = RecordingTracer()
    client = make_client(lambda r: pytest.fail("no SQL for an invalid name"), tracer=tracer)
    assert z.uncache(client, "bad name", reason="test") is False
    assert tracer.events[-1]["action"] == "uncache" and tracer.events[-1]["status"] == "error"
