"""Every sponsor call's trace line records what went over the wire (httpx.MockTransport; no network).

Fields: endpoint (path only), method, service_host (host:port, no userinfo), http_status (None when
no reply came back), request_bytes and response_bytes (None without a reply), for the last
attempt. Sample mode sends nothing and must not invent them. Secrets never appear in a line.
"""

import json

import httpx
import pytest

from tiresias_agent.adapters import zetaris as z
from tiresias_agent.adapters.cognition import CognitionClient, CognitionConfig
from tiresias_agent.adapters.nightshift import (
    ModelProtocolError,
    ModelUnavailable,
    NightshiftClient,
    NightshiftConfig,
)

EXCHANGE = ("endpoint", "method", "service_host", "http_status", "request_bytes", "response_bytes")
ZKEY = "zk-test-secret-exchange-0123456789"
NTOKEN = "rt-test-secret-exchange-0123456789"


class RecordingTracer:
    run_id = "run-exchange-1"

    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def replying(sent, response):
    """A handler that records the request and reply sizes it saw, then replies."""

    def handler(request):
        sent["request"] = len(request.content)
        sent["response"] = len(response.content)
        return response

    return handler


def assert_no_secret(events, *secrets):
    dumped = json.dumps(events, default=str)
    for secret in secrets:
        assert secret not in dumped


# --- Zetaris ------------------------------------------------------------------------------------


def zetaris(handler, tracer, max_retries=0, rest_url="http://zetaris.test:8888/api/v1.0"):
    config = z.ZetarisConfig(
        rest_url=rest_url, org_id="1", api_key=ZKEY, timeout_seconds=5, max_retries=max_retries
    )
    return z.ZetarisClient(
        config, tracer=tracer, transport=httpx.MockTransport(handler), sleep=lambda s: None
    )


def test_zetaris_success_line_records_the_exchange():
    sent = {}
    body = {"headers": ["n"], "data": [[1], [2]], "total": 2}
    tracer = RecordingTracer()
    zetaris(replying(sent, httpx.Response(200, json=body)), tracer).run_sql(
        "SELECT 1", label="probe"
    )
    (line,) = tracer.events
    assert line["status"] == "success" and line["rows"] == 2 and line["sql"] == "SELECT 1"
    assert line["endpoint"] == "/api/v1.0/sql-editor/sqls/run"
    assert line["method"] == "POST"
    assert line["service_host"] == "zetaris.test:8888"
    assert line["http_status"] == 200
    assert line["request_bytes"] == sent["request"] > 0
    assert line["response_bytes"] == sent["response"] > 0
    assert_no_secret(tracer.events, ZKEY)


def test_zetaris_http_error_line_carries_the_status_and_reply_size():
    reply = "Table or view not found: tiresias.nope"
    tracer = RecordingTracer()
    with pytest.raises(z.SourceError):
        zetaris(lambda r: httpx.Response(400, text=reply), tracer).run_sql(
            "SELECT * FROM tiresias.nope", label="probe"
        )
    (line,) = tracer.events
    assert line["status"] == "error"
    assert line["http_status"] == 400
    assert line["response_bytes"] == len(reply.encode())
    assert line["endpoint"] == "/api/v1.0/sql-editor/sqls/run" and line["request_bytes"] > 0
    assert_no_secret(tracer.events, ZKEY)


def test_zetaris_transport_error_has_no_status_and_describes_the_last_attempt():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    tracer = RecordingTracer()
    with pytest.raises(z.SourceError):
        zetaris(handler, tracer, max_retries=2).run_sql("SELECT 1", label="probe")
    (line,) = tracer.events
    assert line["retry_count"] == 2
    assert line["http_status"] is None and line["response_bytes"] is None
    assert line["request_bytes"] > 0 and line["method"] == "POST"
    assert_no_secret(tracer.events, ZKEY)


def test_zetaris_service_host_drops_userinfo():
    tracer = RecordingTracer()
    url = "http://svc:p4ss-word@zetaris.test:8888/api/v1.0"
    zetaris(
        lambda r: httpx.Response(200, json={"headers": [], "data": []}), tracer, rest_url=url
    ).run_sql("SELECT 1", label="probe")
    (line,) = tracer.events
    assert line["service_host"] == "zetaris.test:8888"
    assert_no_secret(tracer.events, "p4ss-word", ZKEY)


def test_zetaris_line_not_sent_when_down_has_no_exchange():
    tracer = RecordingTracer()
    client = zetaris(lambda r: httpx.Response(200, json={}), tracer)
    client.mark_down("liveness probe failed")
    with pytest.raises(z.SourceError):
        client.run_sql("SELECT 1", label="probe")
    (line,) = tracer.events
    assert not set(EXCHANGE) & set(line)


def test_sample_mode_lines_invent_no_exchange():
    tracer = RecordingTracer()
    z.SampleSource(tracer=tracer).concept_series("ge", "Revenues")
    assert tracer.events
    for line in tracer.events:
        assert not set(EXCHANGE) & set(line), line


# --- Nightshift ---------------------------------------------------------------------------------


NCFG = NightshiftConfig(base_url="http://vm.test:8088/v1", token=NTOKEN, timeout_seconds=5)


def completion(text="hi"):
    return {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
        "usage": {"total_tokens": 4},
        "meterless": {"routed_model": "nvidia/nemotron-x", "receipt_id": "r-1"},
    }


def nightshift(handler, tracer, config=NCFG):
    return NightshiftClient(config, tracer=tracer, transport=httpx.MockTransport(handler))


def test_nightshift_success_line_records_the_exchange():
    sent = {}
    tracer = RecordingTracer()
    nightshift(replying(sent, httpx.Response(200, json=completion())), tracer).chat(
        [{"role": "user", "content": "x"}], lane="fast"
    )
    (line,) = tracer.events
    assert line["routed_model"] == "nvidia/nemotron-x"
    assert line["endpoint"] == "/v1/chat/completions"
    assert line["method"] == "POST"
    assert line["service_host"] == "vm.test:8088"
    assert line["http_status"] == 200
    assert line["request_bytes"] == sent["request"] > 0
    assert line["response_bytes"] == sent["response"] > 0
    assert_no_secret(tracer.events, NTOKEN)


def test_nightshift_rejected_request_line_carries_the_status():
    sent = {}
    reply = httpx.Response(404, json={"error": {"message": "model_not_found"}})
    tracer = RecordingTracer()
    with pytest.raises(ModelProtocolError):
        nightshift(replying(sent, reply), tracer).chat(
            [{"role": "user", "content": "x"}], lane="deep"
        )
    (line,) = tracer.events
    assert line["status"] == "error"
    assert line["http_status"] == 404
    assert line["response_bytes"] == sent["response"] > 0
    assert_no_secret(tracer.events, NTOKEN)


def test_nightshift_unreachable_has_no_status():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    tracer = RecordingTracer()
    with pytest.raises(ModelUnavailable):
        nightshift(handler, tracer).chat([{"role": "user", "content": "x"}], lane="fast")
    (line,) = tracer.events
    assert line["http_status"] is None and line["response_bytes"] is None
    assert line["endpoint"] == "/v1/chat/completions" and line["request_bytes"] > 0
    assert_no_secret(tracer.events, NTOKEN)


def test_nightshift_not_configured_sends_nothing_and_invents_no_exchange():
    tracer = RecordingTracer()
    config = NightshiftConfig(base_url="", token="")
    with pytest.raises(ModelUnavailable):
        nightshift(lambda r: httpx.Response(200, json=completion()), tracer, config).chat(
            [{"role": "user", "content": "x"}], lane="fast"
        )
    (line,) = tracer.events
    assert not set(EXCHANGE) & set(line)


def test_nightshift_exchange_does_not_leak_between_calls():
    tracer = RecordingTracer()
    client = nightshift(lambda r: httpx.Response(200, json=completion()), tracer)
    client.chat([{"role": "user", "content": "x"}], lane="fast")
    client.config = NightshiftConfig(base_url="", token="")  # the next call sends nothing
    with pytest.raises(ModelUnavailable):
        client.chat([{"role": "user", "content": "x"}], lane="fast")
    assert "endpoint" in tracer.events[0] and "endpoint" not in tracer.events[1]


# --- Cognition (H-MEM) --------------------------------------------------------------------------


def cognition(handler, tracer):
    config = CognitionConfig(url="http://cognition.test:8100", timeout_s=1.0, retries=1)
    return CognitionClient(
        config, run_id="run-exchange-1", tracer=tracer, transport=httpx.MockTransport(handler)
    )


def test_cognition_recall_line_records_the_exchange():
    sent = {}
    tracer = RecordingTracer()
    reply = httpx.Response(200, json={"lessons": [], "query": "q"})
    cognition(replying(sent, reply), tracer).recall("why did revenue change")
    (line,) = tracer.events
    assert line["endpoint"] == "/memory/recall"
    assert line["method"] == "POST"
    assert line["service_host"] == "cognition.test:8100"
    assert line["http_status"] == 200
    assert line["request_bytes"] == sent["request"] > 0
    assert line["response_bytes"] == sent["response"] > 0


def test_cognition_endpoint_is_the_path_without_query_string():
    tracer = RecordingTracer()
    client = cognition(lambda r: httpx.Response(200, json={"lessons": [], "total": 0}), tracer)
    client.list_memory(namespace="secret-ns")  # GET /memory?namespace=secret-ns
    (line,) = tracer.events
    assert line["endpoint"] == "/memory" and line["method"] == "GET"
    assert "secret-ns" not in line["endpoint"]


def test_cognition_unreachable_degraded_line_has_no_status():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    tracer = RecordingTracer()
    result = cognition(handler, tracer).recall("q")
    assert result.available is False
    (line,) = tracer.events
    assert line["status"] == "degraded"
    assert line["http_status"] is None and line["response_bytes"] is None
    assert line["endpoint"] == "/memory/recall"


def test_cognition_server_error_line_carries_the_status():
    tracer = RecordingTracer()
    cognition(lambda r: httpx.Response(503, json={"message": "busy"}), tracer).recall("q")
    (line,) = tracer.events
    assert line["status"] == "degraded" and line["http_status"] == 503
