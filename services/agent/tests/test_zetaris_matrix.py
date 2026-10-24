"""Zetaris error handling as a known shape (zetaris/README.md, "Error handling").

One test per row of the matrix: the happy path, the scenarios we have seen (outage, dead engine,
known fatal errors) and the catch-all (an unclassified reply is an outage, traced distinctly).
Each asserts the result, the trace status and cause, and whether the error is recoverable.
No network: every reply comes from httpx.MockTransport.
"""

import json
from pathlib import Path

import httpx
import pytest
from fake_zetaris import FakeZetaris
from loop_helpers import request

from tiresias_agent.adapters import catalog as cat
from tiresias_agent.adapters import zetaris as z
from tiresias_agent.config import WarmSettings
from tiresias_agent.models import RunResult
from tiresias_agent.orchestrator import run_investigation
from tiresias_agent.trace import Tracer

KEY = "zk-matrix-secret-0123456789abcdef"
UA = "Tiresias test test@example.com"
STOPPED = (
    "org.apache.hive.service.cli.HiveSQLException: java.lang.IllegalStateException: "
    "Cannot call methods on a stopped SparkContext."
)
GE_TICKERS = [
    ("GE", 40545, "GENERAL ELECTRIC CO"),
    ("GEHC", 1932393, "GE HealthCare Technologies Inc."),
    ("GEV", 1996810, "GE Vernova Inc."),
]
GE_ALIASES = {"ge": "ge", "gehc": "gehc", "gev": "gev"}


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
    def refuse(self, req):
        raise AssertionError(f"real HTTP request to {req.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


class RecordingTracer:
    run_id = "run-matrix-1"

    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})

    def with_status(self, status):
        return [e for e in self.events if e["status"] == status]


def make_client(handler, tracer=None, max_retries=2):
    config = z.ZetarisConfig(
        rest_url="http://zetaris.test/api/v1.0",
        api_key=KEY,
        timeout_seconds=5.0,
        max_retries=max_retries,
    )
    return z.ZetarisClient(
        config, tracer=tracer, transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def counting(reply):
    """A handler that answers every statement with ``reply`` (a Response, or an exception to
    raise) and counts the requests."""
    calls = []

    def handler(req):
        calls.append(req)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return handler, calls


def degraded_read(handler):
    """concept_series through the live FallbackSource: (evidence, tracer)."""
    tracer = RecordingTracer()
    source = z.make_source(False, tracer, client=make_client(handler, tracer))
    return source.concept_series("ge", "Revenues"), tracer


def assert_served_from_sample(facts, tracer, cause, *, reply_failed=True):
    """The read came from the sample, one degraded line with ``cause``; when the reply itself
    failed, the client's error line carries the same cause."""
    assert facts and facts.source == "sample" and facts.degraded
    degraded = tracer.with_status("degraded")
    assert len(degraded) == 1 and degraded[0]["source"] == "sample"
    assert degraded[0]["cause"] == cause
    if reply_failed:
        error = [e for e in tracer.events if e["status"] == "error" and e["source"] == "zetaris"]
        assert error and error[-1]["cause"] == cause
    return degraded[0]


# --- happy path --------------------------------------------------------------------------------


def test_happy_path_query_is_live_with_success_lines():
    fake = FakeZetaris(GE_TICKERS, GE_ALIASES, views={"ge_revenues"})
    tracer = RecordingTracer()
    source = z.make_source(False, tracer, client=fake.client(tracer))
    facts = source.concept_series("ge", "Revenues")
    assert facts and facts.source == "zetaris" and not facts.degraded and facts.error is None
    assert {e["status"] for e in tracer.events} == {"success"}
    assert [e["action"] for e in tracer.events] == ["list_views", "concept_series"]


def test_happy_path_register_bootstraps_one_entity_with_success_lines():
    fake = FakeZetaris(GE_TICKERS, GE_ALIASES)
    tracer = RecordingTracer()
    entity = z.Entity("ge", "0000040545", "GENERAL ELECTRIC CO")
    report = z.bootstrap(
        fake.client(tracer), sec_user_agent=UA, entities=[entity], tables=("facts",)
    )
    statuses = {e["status"] for e in report}
    assert statuses <= {"created", "exists", "cached", "replaced", "absent"}
    assert {"created", "cached", "replaced"} <= statuses
    assert {e["status"] for e in tracer.events} == {"success"}  # "exists" / "absent" too
    assert "TIRESIAS_SEC.ge_facts_raw" in json.dumps(report) and "ge_facts_raw" in fake.tables


def test_happy_path_warm_recaches_with_success_lines():
    tables = {"ge_facts_raw": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000040545.json"}
    fake = FakeZetaris(GE_TICKERS, GE_ALIASES, tables=tables)
    tracer = RecordingTracer()
    report = z.warm(fake.client(tracer), settings=WarmSettings(), cache=z.FactsCache())
    assert [e["status"] for e in report] == ["warmed"]
    assert {e["status"] for e in tracer.events} == {"success"}


# --- outage: served from the sample, traced degraded -------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("connection refused"),
        httpx.Response(500, text="Internal Server Error"),
        httpx.Response(502, text="Bad Gateway"),
        httpx.Response(503, text="Service Unavailable"),
    ],
    ids=["timeout", "connection-refused", "500", "502", "503"],
)
def test_outage_is_recoverable_retried_and_served_from_the_sample(reply):
    handler, calls = counting(reply)
    facts, tracer = degraded_read(handler)
    line = assert_served_from_sample(facts, tracer, "outage")
    assert len(calls) == 3 and line["retry_count"] == 2  # an outage is retried (max_retries 2)
    with pytest.raises(z.SourceError) as info:
        make_client(handler, max_retries=0).run_sql("SELECT 1", label="x")
    assert info.value.recoverable and info.value.cause == "outage"


# --- dead engine: served from the sample, the reason says restart the driver --------------------


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(400, json={"msg": STOPPED}),
        httpx.Response(200, json={"headers": [], "data": [], "msg": STOPPED}),
        httpx.Response(200, json={"headers": ["x"], "data": [["1"]], "msg": STOPPED}),
    ],
    ids=["400-body", "200-empty", "200-with-rows"],
)
def test_dead_engine_is_an_outage_whose_reason_says_restart_the_driver(reply):
    handler, calls = counting(reply)
    facts, tracer = degraded_read(handler)
    assert_served_from_sample(facts, tracer, "dead_engine")
    assert "restart the driver" in facts.error and len(calls) == 1  # not retried
    with pytest.raises(z.SourceError) as info:
        make_client(handler).run_sql("SELECT 1", label="x")
    assert info.value.recoverable and info.value.dead_engine


# --- known fatal: raised, clear, never from the sample -----------------------------------------


@pytest.mark.parametrize(
    ("reply", "cause", "says"),
    [
        (httpx.Response(401, json={"msg": "token expired"}), "auth", "HTTP 401"),
        (httpx.Response(403, text="no"), "auth", "HTTP 403"),
        (
            httpx.Response(
                400, json={"msg": "org.apache.spark.sql.catalyst.parser.ParseException"}
            ),
            "sql",
            "ParseException",
        ),
        (
            httpx.Response(400, json={"msg": "org.apache.spark.sql.AnalysisException: x"}),
            "sql",
            "AnalysisException",
        ),
        (
            httpx.Response(400, json={"msg": "[FIELD_NOT_FOUND] No such struct field Revenues"}),
            "sql",
            "FIELD_NOT_FOUND",
        ),
    ],
    ids=["401", "403", "ParseException", "AnalysisException", "FIELD_NOT_FOUND-on-a-query"],
)
def test_known_fatal_is_raised_clearly_and_never_served_from_the_sample(reply, cause, says):
    handler, calls = counting(reply)
    tracer = RecordingTracer()
    source = z.make_source(False, tracer, client=make_client(handler, tracer))
    with pytest.raises(z.SourceError) as info:
        source.concept_series("ge", "Revenues")  # list_views is the query that fails
    err = info.value
    assert not err.recoverable and err.cause == cause and says in str(err)
    assert len(calls) == 1  # never retried
    assert not tracer.with_status("degraded")
    error = tracer.with_status("error")
    assert len(error) == 1 and error[0]["cause"] == cause and error[0]["http_status"]


def test_known_fatal_validation_fails_before_sending():
    handler, calls = counting(httpx.Response(200, json={"headers": [], "data": []}))
    tracer = RecordingTracer()
    source = z.make_source(False, tracer, client=make_client(handler, tracer))
    with pytest.raises(ValueError):
        source.concept_series("ge; DROP", "Revenues")  # a bad {{ticker}} placeholder
    with pytest.raises(KeyError):
        z.render("SELECT {{x}}", y="1")  # a missing template parameter
    assert calls == [] and not tracer.with_status("degraded")


# --- unclassified: served from the sample, traced with status and excerpt ----------------------


HTML = "<html><head><title>502 Proxy Error</title></head><body>teapot</body></html>"


@pytest.mark.parametrize(
    ("reply", "status", "excerpt"),
    [
        (httpx.Response(418, json={"msg": "I'm a teapot"}), 418, "teapot"),
        (httpx.Response(422, json={"msg": "Unprocessable: frobnicated"}), 422, "frobnicated"),
        (httpx.Response(429, text="slow down"), 429, "slow down"),
        (httpx.Response(200, text='{"headers": ["x"], "da'), 200, '{"headers"'),
        (httpx.Response(400, text=""), 400, None),
        (httpx.Response(200, text=HTML, headers={"content-type": "text/html"}), 200, "<html>"),
        (httpx.Response(404, text=HTML, headers={"content-type": "text/html"}), 404, "<html>"),
        (httpx.Response(200, json={"headers": "x", "data": {"y": 1}}), 200, '"headers"'),
        (httpx.Response(200, json=[{"headers": ["x"], "data": []}]), 200, "[{"),
    ],
    ids=[
        "418",
        "422",
        "429",
        "malformed-json",
        "empty-body",
        "html-200",
        "html-404",
        "2xx-unexpected-shape",
        "2xx-list",
    ],
)
def test_unclassified_is_an_outage_traced_with_status_and_excerpt(reply, status, excerpt):
    handler, calls = counting(reply)
    facts, tracer = degraded_read(handler)
    line = assert_served_from_sample(facts, tracer, "unclassified")
    assert line["http_status"] == status and len(calls) == 1  # not retried
    if excerpt:
        assert excerpt in line["excerpt"] and len(line["excerpt"]) <= z.EXCERPT_CHARS
    assert KEY not in json.dumps(tracer.events)
    with pytest.raises(z.SourceError) as info:
        make_client(handler).run_sql("SELECT 1", label="x")
    assert info.value.recoverable and info.value.cause == "unclassified"


def test_unclassified_excerpt_is_scrubbed_of_the_key():
    reply = httpx.Response(418, text=f"echo Bearer {KEY} back")
    facts, tracer = degraded_read(counting(reply)[0])
    assert_served_from_sample(facts, tracer, "unclassified")
    assert KEY not in json.dumps(tracer.events) and z.REDACTED in facts.error


def test_unclassified_row_shape_and_unexpected_exception_are_wrapped_as_outages():
    # A 2xx whose row does not parse (a value that is not a number) ...
    fake = FakeZetaris(GE_TICKERS, GE_ALIASES, views={"ge_revenues"})

    def bad_rows(req):
        if "SELECT concept" in json.loads(req.content)["sql"]:
            cols = ["concept", "unit", "val", "period_start", "period_end", "fy", "fp", "form"]
            row = ["Revenues", "USD", "lots", None, "2022-12-31", 2022, "FY", "10-K"]
            return z_ok([*cols, "filed", "accn", "frame"], [[*row, None, None, None]])
        return fake(req)

    facts, tracer = degraded_read(bad_rows)
    line = assert_served_from_sample(facts, tracer, "unclassified", reply_failed=False)
    assert "lots" in line["excerpt"]
    # ... and an exception type no adapter rule expects (rule 8: wrapped at the boundary).
    facts, tracer = degraded_read(counting(RuntimeError("boom"))[0])
    assert facts.degraded and facts.source == "sample"
    assert tracer.with_status("degraded")[0]["cause"] == "unclassified"
    assert "RuntimeError" in facts.error


def test_unclassified_transport_oserror_is_an_outage_not_a_before_sending_error():
    # An OSError from the transport is not our input: it degrades, in evidence and discovery reads.
    facts, tracer = degraded_read(counting(OSError("socket exploded after send"))[0])
    assert facts.degraded and facts.source == "sample" and "OSError" in facts.error
    assert tracer.with_status("degraded")[0]["cause"] == "unclassified"
    tracer = RecordingTracer()
    client = make_client(counting(OSError("socket exploded"))[0], tracer)
    rows = cat.make_discovery(False, tracer, client=client).lookup(tickers=["GE"])
    assert rows and rows.degraded and tracer.with_status("degraded")[0]["cause"] == "unclassified"
    # A missing template file is still ours, raised before sending (a ValueError, never degraded).
    with pytest.raises(z.TemplateError):
        z.load_template("no_such_template.sql")


def test_unclassified_reply_a_caller_expects_stays_benign():
    reply = httpx.Response(409, json={"msg": "Conflict: duplicate object"})
    tracer = RecordingTracer()
    client = make_client(counting(reply)[0], tracer)
    with pytest.raises(z.SourceError) as info:
        client.run_sql(
            "CREATE X", label="c", expected=lambda e: "dup" if "duplicate" in e.detail else None
        )
    assert not info.value.recoverable and info.value.cause == "refused"
    assert tracer.events[-1]["status"] == "success" and tracer.events[-1]["outcome"] == "dup"


def test_unclassified_discovery_read_is_served_from_the_sample():
    tracer = RecordingTracer()
    client = make_client(counting(httpx.Response(418, text="teapot"))[0], tracer)
    rows = cat.make_discovery(False, tracer, client=client).lookup(tickers=["GE"])
    assert rows and rows.degraded and rows.source == "sample"
    line = tracer.with_status("degraded")[0]
    assert line["cause"] == "unclassified" and line["http_status"] == 418


def test_classification_lives_in_one_place():
    assert set(z.RECOVERABLE_CAUSES) < set(z.CAUSES)
    assert z.classify_reply(400, STOPPED) == "dead_engine"
    assert z.classify_reply(503, "already exists") == "outage"
    assert z.classify_reply(401, "") == z.classify_reply(400, "Unauthorized") == "auth"
    assert z.classify_reply(400, "[TABLE_OR_VIEW_NOT_FOUND] t") == "sql"
    assert z.classify_reply(400, 'A table named "t" already exists.') == "refused"
    assert z.classify_reply(400, "java.lang.NullPointerException at Foo.bar") == "unclassified"
    for sigs in (z.AUTH_SIGNATURES, z.SQL_SIGNATURES, z.REFUSED_SIGNATURES):
        assert all(s == s.lower() for s in sigs)
    # The liveness probe does not call Zetaris down on a reply it cannot classify.
    client = make_client(counting(httpx.Response(418, text="teapot"))[0])
    assert z.check_liveness(client, probe=z.LivenessProbe()).state == "inconclusive"
    assert client.down_reason is None


def z_ok(headers, rows):
    return httpx.Response(200, json={"headers": headers, "data": rows})


# --- a failure in the middle of a live run -----------------------------------------------------


MID_RUN = {
    "outage": (httpx.Response(503, text="Service Unavailable"), "degraded"),
    "fatal": (httpx.Response(400, json={"msg": "[PARSE_SYNTAX_ERROR] near 'x'"}), "error"),
    "unclassified": (httpx.Response(418, text="I'm a teapot"), "degraded"),
    "unexpected-exception": (RuntimeError("transport bug"), "degraded"),
}


@pytest.mark.parametrize("kind", list(MID_RUN), ids=list(MID_RUN))
def test_a_failure_mid_run_ends_in_a_structured_result(kind, tmp_path, monkeypatch, no_real_http):
    """GE example 01 live: the entity resolves and the frame reads live, then every filings
    read fails with one class of error. The run returns a RunResult; the trace shows the cause."""
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    reply, status = MID_RUN[kind]
    fake = FakeZetaris(GE_TICKERS, GE_ALIASES)
    failed = []

    def handler(req):
        sql = " ".join(json.loads(req.content)["sql"].split())
        if "FROM tiresias." in sql and "_filings" in sql and sql.startswith("SELECT"):
            failed.append(sql)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return fake(req)

    req = request("01")
    req.options.sample_mode = False
    tracer = Tracer(f"run-matrix-{kind}", logs_dir=tmp_path, defaults={"mode": "live"})
    client = make_client(handler, tracer, max_retries=0)
    source = z.make_source(False, tracer, client=client)
    result = run_investigation(req, source=source, tracer=tracer, zetaris_client=client)
    assert isinstance(result, RunResult) and result.end_state
    assert failed, "the run never read filings"
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    cause = {"outage": "outage", "fatal": "sql"}.get(kind, "unclassified")
    hits = [x for x in lines if x.get("cause") == cause and x["status"] == status]
    assert hits, f"no {status} line with cause {cause}"
    if kind == "fatal":
        # Never served from the sample: the filing tests cannot run, so the run stops at the
        # frontier with a reason (the contract's partial result), not an exception.
        assert not [x for x in lines if x["status"] == "degraded" and x["action"] == "filings"]
        assert result.status == "partial" and result.end_state == "evidence-frontier"
        assert result.information_need and result.information_need.reason
    else:
        # The sample served the filings reads (GE is in it), so the run still finds the cause.
        assert result.error is None and result.end_state == "actionable"
        if kind != "unexpected-exception":  # no reply, so no HTTP status
            assert all(x.get("http_status") for x in hits if x["source"] == "sample")
