"""Zetaris cache warm-up (BACKLOG B-20): `warm`, the CLI, warm on start and GET /health.

No network: every Zetaris reply comes from httpx.MockTransport.
"""

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tiresias_agent import app as app_module
from tiresias_agent.adapters import zetaris as z
from tiresias_agent.config import Settings, WarmSettings

KEY = "zk-warm-secret-0123456789abcdef"
RealClient = z.ZetarisClient
CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "agent.yaml"
TABLES = {
    "fts_0123abcd_raw": "https://efts.sec.gov/LATEST/search-index?q=x",
    "gev_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0001996810.json",
    "mmm_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000066740.json",
    "ge_submissions_raw": "https://data.sec.gov/submissions/CIK0000040545.json",
    "ge_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000040545.json",
    "sec_tickers_raw": "https://www.sec.gov/files/company_tickers_exchange.json",
}
ORDER = [  # showcase entities (facts, then submissions), the ticker map, other entities
    "ge_facts_raw",
    "ge_submissions_raw",
    "gev_facts_raw",
    "sec_tickers_raw",
    "mmm_facts_raw",
]


class RecordingTracer:
    run_id = "warm-test-1"

    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def ok(headers, rows):
    return httpx.Response(200, json={"headers": headers, "data": rows, "total": len(rows)})


class FakeZetaris:
    """SHOW LIGHTNING TABLES lists TABLES; CACHE TABLE answers per table from `replies`."""

    def __init__(self, replies=None, tables=TABLES, on_cache=None):
        self.replies = replies or {}
        self.tables = tables
        self.on_cache = on_cache
        self.sql = []
        self.timeouts = []

    def __call__(self, request):
        sql = " ".join(json.loads(request.content)["sql"].split())
        self.sql.append(sql)
        if sql.startswith("SHOW LIGHTNING TABLES"):
            return ok(["name", "source_table"], [[n, e] for n, e in self.tables.items()])
        if sql.startswith("CACHE TABLE"):
            self.timeouts.append(request.extensions["timeout"]["read"])
            table = sql.split(".")[-1]
            if self.on_cache:
                self.on_cache(table)
            reply = self.replies.get(table)
            if reply == "timeout":
                raise httpx.ReadTimeout("slow", request=request)
            return reply or ok([], [])
        return httpx.Response(400, text="unexpected statement")

    def cached(self):
        return [s.split(".")[-1] for s in self.sql if s.startswith("CACHE TABLE")]


def make_client(fake, tracer=None, max_retries=2):
    config = z.ZetarisConfig(
        rest_url="http://zetaris.test/api/v1.0",
        api_key=KEY,
        timeout_seconds=20.0,
        max_retries=max_retries,
    )
    return RealClient(
        config, tracer=tracer, transport=httpx.MockTransport(fake), sleep=lambda s: None
    )


# --- warm -------------------------------------------------------------------------------------


def test_warm_recaches_every_registered_raw_table_once_in_priority_order():
    fake, tracer = FakeZetaris(), RecordingTracer()
    status = z.WarmStatus()
    report = z.warm(make_client(fake, tracer), settings=WarmSettings(), status=status)
    assert fake.cached() == ORDER  # facts (showcase first), submissions, tickers; no fts table
    assert [e["table"] for e in report] == [f"TIRESIAS_SEC.{t}" for t in ORDER]
    assert {e["status"] for e in report} == {"warmed"}
    assert [e["kind"] for e in report] == ["facts", "submissions", "facts", "tickers", "facts"]
    assert fake.sql[0] == "SHOW LIGHTNING TABLES TIRESIAS_SEC"
    assert not [s for s in fake.sql if s.split()[0] in {"CREATE", "DROP", "REPLACE"}]
    snap = status.snapshot()
    assert snap["state"] == "warm" and snap["done"] == snap["total"] == 5
    assert snap["counts"] == {"warmed": 5}


def test_each_statement_gets_the_warm_timeout_not_the_source_timeout():
    fake = FakeZetaris()
    z.warm(make_client(fake), settings=WarmSettings(timeout_seconds=120.0, budget_seconds=600.0))
    assert fake.timeouts == [120.0] * 5


def test_a_failing_table_is_reported_and_the_rest_still_warm():
    fake = FakeZetaris(
        replies={
            "mmm_facts_raw": "timeout",
            "ge_submissions_raw": httpx.Response(400, text="PARSE_SYNTAX_ERROR near CACHE"),
        }
    )
    tracer = RecordingTracer()
    status = z.WarmStatus()
    report = z.warm(make_client(fake, tracer), settings=WarmSettings(), status=status)
    by = {e["table"].split(".")[-1]: e for e in report}
    assert by["mmm_facts_raw"]["status"] == "failed" and by["mmm_facts_raw"]["recoverable"]
    assert "timed out after 120 s" in by["mmm_facts_raw"]["error"]
    assert by["ge_submissions_raw"]["status"] == "failed"
    assert by["ge_submissions_raw"]["recoverable"] is False
    assert [by[t]["status"] for t in ("ge_facts_raw", "gev_facts_raw", "sec_tickers_raw")] == [
        "warmed"
    ] * 3
    assert fake.cached().count("mmm_facts_raw") == 1  # a timed-out refetch is not retried
    assert status.snapshot()["state"] == "partial"


def test_two_failures_in_a_row_halt_the_rest():
    down = httpx.Response(
        400, text="IllegalStateException: Cannot call methods on a stopped SparkContext"
    )
    fake = FakeZetaris(replies={"ge_submissions_raw": down, "gev_facts_raw": down})
    status = z.WarmStatus()
    report = z.warm(make_client(fake), settings=WarmSettings(), status=status)
    assert [e["status"] for e in report] == ["warmed", "failed", "failed", "skipped", "skipped"]
    assert {e.get("reason") for e in report[3:]} == {"halted"}
    assert fake.cached() == ORDER[:3]  # nothing sent after the second failure in a row
    assert status.snapshot()["state"] == "partial"


def test_max_tables_caps_the_pass_and_lists_the_rest_as_capped():
    fake = FakeZetaris()
    status = z.WarmStatus()
    report = z.warm(make_client(fake), settings=WarmSettings(max_tables=2), status=status)
    assert fake.cached() == ORDER[:2]
    assert [e["status"] for e in report] == ["warmed", "warmed", "capped", "capped", "capped"]
    snap = status.snapshot()
    assert snap["state"] == "warm" and snap["total"] == 2  # capped is by design, not partial
    assert snap["counts"] == {"warmed": 2, "capped": 3}


def test_warm_order_puts_discovery_only_submissions_last():
    tables = [
        "gd_submissions_raw",  # a scout candidate: no facts table
        "ibm_submissions_raw",
        "ibm_facts_raw",
        "sec_tickers_raw",
        "gehc_submissions_raw",
        "ge_facts_raw",
        "fts_0123abcd_raw",
        "gehc_facts_raw",
    ]
    assert z.warm_order(tables) == [
        "ge_facts_raw",
        "gehc_facts_raw",
        "gehc_submissions_raw",
        "sec_tickers_raw",
        "ibm_facts_raw",
        "ibm_submissions_raw",
        "gd_submissions_raw",
    ]


def test_the_overall_budget_skips_the_remaining_tables():
    now = [0.0]

    def clock():
        return now[0]

    def on_cache(table):
        now[0] += 40.0  # each refetch takes 40 s of a 100 s budget

    fake = FakeZetaris(on_cache=on_cache)
    status = z.WarmStatus()
    report = z.warm(
        make_client(fake),
        settings=WarmSettings(timeout_seconds=120.0, budget_seconds=100.0),
        status=status,
        clock=clock,
    )
    assert [e["status"] for e in report] == ["warmed", "warmed", "warmed", "skipped", "skipped"]
    assert all(e.get("budget") is True and e["reason"] == "budget" for e in report[3:])
    # the statement timeout is capped at the budget left (100, 60, then 20 s)
    assert fake.timeouts == [100.0, 60.0, 20.0]
    snap = status.snapshot()
    assert snap["state"] == "partial" and snap["done"] == 5
    assert snap["counts"] == {"warmed": 3, "skipped": 2}


def test_trace_statuses_per_statement_and_summary():
    fake = FakeZetaris(
        replies={
            "mmm_facts_raw": httpx.Response(400, text="[TABLE_OR_VIEW_NOT_FOUND] mmm_facts_raw"),
            "gev_facts_raw": httpx.Response(503, text="busy"),
        }
    )
    tracer = RecordingTracer()
    report = z.warm(make_client(fake, tracer), settings=WarmSettings())
    lines = [e for e in tracer.events if e["action"] == "warm:cache"]
    assert len(lines) == 5 and all(e["agent"] == "zetaris_adapter" for e in lines)
    by = {e["sql"].split(".")[-1]: e for e in lines}
    assert by["ge_facts_raw"]["status"] == "success" and "outcome" not in by["ge_facts_raw"]
    assert by["mmm_facts_raw"]["status"] == "success"
    assert by["mmm_facts_raw"]["outcome"] == "absent"  # benign, B-9's expected= mechanism
    assert by["gev_facts_raw"]["status"] == "error"  # a 5xx is never benign
    assert {e["table"].split(".")[-1]: e["status"] for e in report}["mmm_facts_raw"] == "absent"
    summary = tracer.events[-1]
    assert summary["action"] == "warm" and summary["status"] == "degraded"
    assert summary["outcome"] == "partial" and summary["counts"]["failed"] == 1


def test_summary_is_success_when_all_warm_and_error_when_none_warm():
    tracer = RecordingTracer()
    z.warm(make_client(FakeZetaris(), tracer), settings=WarmSettings())
    assert tracer.events[-1]["status"] == "success" and tracer.events[-1]["outcome"] == "warm"
    down = FakeZetaris(replies={t: httpx.Response(503, text="down") for t in TABLES})
    tracer = RecordingTracer()
    status = z.WarmStatus()
    z.warm(make_client(down, tracer, max_retries=0), settings=WarmSettings(), status=status)
    assert tracer.events[-1]["status"] == "error" and status.snapshot()["state"] == "failed"


def test_no_registered_tables_is_warm_with_nothing_to_do():
    status = z.WarmStatus()
    report = z.warm(make_client(FakeZetaris(tables={})), settings=WarmSettings(), status=status)
    assert report == [] and status.snapshot()["state"] == "warm"


def test_unreadable_table_list_raises_and_marks_status_failed():
    def handler(request):
        return httpx.Response(401, text="bad token")

    status = z.WarmStatus()
    with pytest.raises(z.SourceError):
        z.warm(make_client(handler), settings=WarmSettings(), status=status)
    assert status.snapshot()["state"] == "failed"


def test_api_key_never_in_report_status_or_trace_lines():
    def leak(request):
        return httpx.Response(400, text=f"denied for {request.headers['Authorization']}")

    class Leaky(FakeZetaris):
        def __call__(self, request):
            sql = json.loads(request.content)["sql"]
            if sql.startswith("CACHE TABLE") and "gev_facts_raw" in sql:
                return leak(request)
            return super().__call__(request)

    tracer = RecordingTracer()
    status = z.WarmStatus()
    report = z.warm(make_client(Leaky(), tracer), settings=WarmSettings(), status=status)
    texts = [json.dumps(report), json.dumps(status.snapshot())]
    texts += [json.dumps(e, default=str) for e in tracer.events]
    assert any("gev_facts_raw" in t and "failed" in t for t in texts[:1])
    assert all(KEY not in t for t in texts)


def test_warm_settings_from_env_are_clamped():
    s = WarmSettings.from_env({})
    assert (s.timeout_seconds, s.budget_seconds, s.max_tables, s.on_start) == (
        120.0,
        600.0,
        7,
        True,
    )
    s = WarmSettings.from_env(
        {
            "ZETARIS_WARM_TIMEOUT_SECONDS": "9999",
            "ZETARIS_WARM_BUDGET_SECONDS": "1",
            "ZETARIS_WARM_MAX_TABLES": "-3",
            "ZETARIS_WARM_ON_START": "false",
        }
    )
    assert (s.timeout_seconds, s.budget_seconds, s.max_tables, s.on_start) == (
        600.0,
        10.0,
        0,
        False,
    )
    assert WarmSettings.from_env({"ZETARIS_WARM_TIMEOUT_SECONDS": "x"}).timeout_seconds == 120.0


# --- warm on start ----------------------------------------------------------------------------

LIVE_ENV = {"ZETARIS_REST_URL": "http://zetaris.test/api/v1.0", "ZETARIS_API_KEY": KEY}


def test_sample_mode_does_nothing():
    built = []
    status = z.WarmStatus()
    thread = z.warm_on_start(
        sample_mode=True, status=status, env=LIVE_ENV, make_client=lambda: built.append(1)
    )
    assert thread is None and built == []
    assert status.snapshot() == {"state": "off", "reason": "sample mode"}


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({**LIVE_ENV, "ZETARIS_WARM_ON_START": "false"}, "ZETARIS_WARM_ON_START is off"),
        ({"ZETARIS_API_KEY": KEY}, "ZETARIS_REST_URL is not set"),
    ],
)
def test_live_mode_off_when_disabled_or_not_configured(env, reason):
    built = []
    status = z.WarmStatus()
    thread = z.warm_on_start(
        sample_mode=False, status=status, env=env, make_client=lambda: built.append(1)
    )
    assert thread is None and built == [] and status.snapshot()["reason"] == reason


def test_live_mode_warms_in_a_background_thread_without_blocking():
    release = threading.Event()
    fake = FakeZetaris(on_cache=lambda table: release.wait(5))
    status = z.WarmStatus()
    started = time.perf_counter()
    thread = z.warm_on_start(
        sample_mode=False, status=status, env=LIVE_ENV, make_client=lambda: make_client(fake)
    )
    assert time.perf_counter() - started < 1.0  # returned while the first CACHE TABLE blocks
    assert thread is not None and thread.daemon and thread.is_alive()
    assert status.snapshot()["state"] == "warming"
    release.set()
    thread.join(5)
    assert not thread.is_alive() and status.snapshot()["state"] == "warm"
    assert fake.cached() == ORDER


def test_a_crashing_warm_never_raises_and_reports_failed():
    def boom():
        raise RuntimeError(f"cannot reach Zetaris with {KEY}")

    tracer = RecordingTracer()
    status = z.WarmStatus()
    thread = z.warm_on_start(
        sample_mode=False, status=status, env=LIVE_ENV, make_client=boom, tracer=tracer
    )
    thread.join(5)
    snap = status.snapshot()
    assert snap["state"] == "failed" and "RuntimeError" in snap["error"]
    assert KEY not in json.dumps(snap) and KEY not in json.dumps(tracer.events)
    assert tracer.events[-1]["action"] == "warm" and tracer.events[-1]["status"] == "error"


# --- API startup and /health --------------------------------------------------------------------


@pytest.fixture
def fresh_status(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    status = z.WarmStatus()
    monkeypatch.setattr(app_module, "zetaris_cache", status)
    return status


def test_api_startup_in_sample_mode_reports_cache_off(monkeypatch, fresh_status):
    monkeypatch.setattr(app_module, "settings", Settings(sample_mode=True))
    with TestClient(app_module.app) as client:
        body = client.get("/health").json()
    assert body["zetaris_cache"] == {"state": "off", "reason": "sample mode"}


def test_api_startup_in_live_mode_warms_in_background_and_health_shows_progress(
    monkeypatch, fresh_status
):
    release = threading.Event()
    fake = FakeZetaris(on_cache=lambda table: release.wait(5))
    threads = []
    real = app_module.warm_on_start

    def spy(**kwargs):
        t = real(**kwargs, env=LIVE_ENV, make_client=lambda: make_client(fake))
        threads.append(t)
        return t

    monkeypatch.setattr(app_module, "settings", Settings(sample_mode=False))
    monkeypatch.setattr(app_module, "warm_on_start", spy)
    started = time.perf_counter()
    with TestClient(app_module.app) as client:
        assert time.perf_counter() - started < 2.0  # startup did not wait for the warm
        cache = client.get("/health").json()["zetaris_cache"]
        assert cache["state"] == "warming"
        release.set()
        threads[0].join(5)
        cache = client.get("/health").json()["zetaris_cache"]
    assert cache["state"] == "warm" and cache["done"] == cache["total"] == 5


def test_api_startup_survives_a_warm_that_cannot_start(monkeypatch, fresh_status):
    def broken(**kwargs):
        raise RuntimeError("thread limit")

    monkeypatch.setattr(app_module, "settings", Settings(sample_mode=False))
    monkeypatch.setattr(app_module, "warm_on_start", broken)
    with TestClient(app_module.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["zetaris_cache"]["state"] == "failed"


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_warm_prints_one_line_per_table_and_the_counts(monkeypatch, capsys):
    fake = FakeZetaris(replies={"sec_tickers_raw": "timeout"})
    monkeypatch.setattr(
        z, "ZetarisClient", lambda config, tracer=None: make_client(fake, tracer, max_retries=0)
    )
    code = z.main(["warm"])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    tables = [line for line in lines if "table" in line and "status" in line]
    assert [t["table"].split(".")[-1] for t in tables] == ORDER
    assert lines[-1]["warm"] == {"warmed": 4, "failed": 1} and lines[-1]["tables"] == 5
    assert code == 1  # not every table warmed
    assert KEY not in json.dumps(lines)


def test_health_body_matches_the_contract(fresh_status):
    yaml = pytest.importorskip("yaml")
    schemas = yaml.safe_load(CONTRACT.read_text())["components"]["schemas"]
    body = TestClient(app_module.app).get("/health").json()
    assert set(schemas["Health"]["required"]) <= set(body) <= set(schemas["Health"]["properties"])
    cache = schemas["ZetarisCache"]["properties"]
    assert body["zetaris_cache"]["state"] in cache["state"]["enum"]
    assert set(body["zetaris_cache"]) <= set(cache)
