"""Cognition client: request shapes, X-Run-Id, degradation and purge failure (httpx.MockTransport)."""

import json
import os
import uuid

import httpx
import pytest

from tiresias_agent.adapters.cognition import (
    CognitionClient,
    CognitionConfig,
    CognitionError,
    CognitionRequestError,
    CognitionUnavailable,
    Gate,
)

RUN_ID = "run-test-1"
LESSON = {
    "id": "mem-1",
    "content": "Restated revenue: check 8-K item 2.01",
    "namespace": "lessons",
    "confidence": 0.7,
    "tags": ["lesson"],
    "runId": RUN_ID,
}


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, *, agent: str, action: str, status: str = "success", **fields) -> None:
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def make_client(handler, tracer=None, run_id=RUN_ID, retries=1):
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    config = CognitionConfig(url="http://cognition.test", timeout_s=1.0, retries=retries)
    client = CognitionClient(
        config, run_id=run_id, tracer=tracer, transport=httpx.MockTransport(recording)
    )
    return client, calls


def ok(body):
    return lambda request: httpx.Response(200, json=body)


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content) if request.content else {}


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def time_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("COGNITION_URL", "http://cog:8100/")
    monkeypatch.delenv("COGNITION_TIMEOUT_S", raising=False)
    config = CognitionConfig.from_env()
    assert config.url == "http://cog:8100"
    assert config.timeout_s == 10.0


def test_health():
    client, calls = make_client(
        ok({"service": "cognition", "version": "0.2.0", "engine": {"name": "H-MEM"}, "lessons": 2})
    )
    health = client.health()
    assert (calls[0].method, calls[0].url.path) == ("GET", "/health")
    assert calls[0].headers["x-run-id"] == RUN_ID
    assert health.available and health.lessons == 2 and health.engine["name"] == "H-MEM"


def test_recall_request_and_typed_result():
    tracer = RecordingTracer()
    payload = {
        "lessons": [{**LESSON, "relevance": 0.29}],
        "context": "ctx",
        "trace": {"strategy": "minimal", "retrievalReason": "r", "scores": {"mem-1": 0.29}},
    }
    client, calls = make_client(ok(payload), tracer)
    out = client.recall("why was revenue restated?", namespace="lessons", top_n=3)
    request = calls[0]
    assert (request.method, request.url.path) == ("POST", "/memory/recall")
    assert request.headers["x-run-id"] == RUN_ID
    assert body_of(request) == {
        "query": "why was revenue restated?",
        "namespace": "lessons",
        "top_n": 3,
    }
    assert out.available and out.memory_ids == ["mem-1"]
    assert out.lessons[0].relevance == 0.29 and out.lessons[0].run_id == RUN_ID
    assert out.trace["strategy"] == "minimal"
    event = tracer.events[-1]
    assert event["agent"] == "memory_client" and event["action"] == "recall"
    assert event["status"] == "success" and event["memory_ids"] == ["mem-1"]
    assert isinstance(event["duration_ms"], int)


def test_recall_leaves_threshold_to_the_server():
    """The client sends no threshold unless asked and never filters what the server returned."""
    low = {**LESSON, "relevance": 0.01}
    client, calls = make_client(ok({"lessons": [low], "context": "", "trace": {}}))
    out = client.recall("anything")
    assert "threshold" not in body_of(calls[0])
    assert out.memory_ids == ["mem-1"]
    client.recall("anything", threshold=0.1)
    assert body_of(calls[1])["threshold"] == 0.1


@pytest.mark.parametrize("failure", [refuse, time_out], ids=["refused", "timeout"])
def test_unreachable_recall_degrades_and_is_traced(failure):
    tracer = RecordingTracer()
    client, _ = make_client(failure, tracer, retries=0)
    out = client.recall("why?")
    assert out.lessons == [] and out.available is False and out.error
    event = tracer.events[-1]
    assert event["action"] == "recall" and event["status"] == "degraded"
    assert event["memory_ids"] == []


def test_recall_retries_connect_error_once_then_succeeds():
    attempts = iter([refuse, ok({"lessons": [LESSON], "context": "", "trace": {}})])
    client, calls = make_client(lambda request: next(attempts)(request), retries=1)
    assert client.recall("why?").memory_ids == ["mem-1"]
    assert len(calls) == 2


def test_server_error_degrades_recall():
    client, _ = make_client(lambda r: httpx.Response(500, json={"status": "error"}))
    assert client.recall("why?").available is False


def test_remember_request_and_gate_round_trip():
    tracer = RecordingTracer()
    gate = Gate(mode="scripted", decision="approve", decided_by="input.decisions", reason="ok")
    client, calls = make_client(
        ok({"stored": True, "lesson": LESSON, "gate": gate.to_json()}), tracer
    )
    out = client.remember("lesson text", namespace="lessons", agent_id="Critic", gate=gate)
    request = calls[0]
    assert (request.method, request.url.path) == ("POST", "/memory/remember")
    assert request.headers["x-run-id"] == RUN_ID
    assert body_of(request) == {
        "content": "lesson text",
        "namespace": "lessons",
        "run_id": RUN_ID,
        "agent_id": "Critic",
        "gate": {
            "mode": "scripted",
            "decision": "approve",
            "decidedBy": "input.decisions",
            "reason": "ok",
        },
    }
    assert out.stored and out.lesson.id == "mem-1" and out.gate == gate
    assert tracer.events[-1]["memory_ids"] == ["mem-1"]


def test_remember_gate_reject_is_traced_as_rejected():
    tracer = RecordingTracer()
    gate = {"mode": "auto", "decision": "reject", "decidedBy": "policy"}
    client, _ = make_client(ok({"stored": False, "gate": gate}), tracer)
    out = client.remember("x", gate=Gate("auto", "reject", "policy"))
    assert out.stored is False and out.available is True
    assert tracer.events[-1]["status"] == "rejected"


def test_unreachable_remember_and_feedback_report_not_stored():
    tracer = RecordingTracer()
    client, calls = make_client(refuse, tracer)
    remembered = client.remember("lesson")
    applied = client.feedback("mem-1", "helpful")
    assert remembered.stored is False and remembered.available is False
    assert applied.applied is False and applied.available is False
    assert [e["status"] for e in tracer.events] == ["degraded", "degraded"]
    assert len(calls) == 2, "writes are not retried"


def test_feedback_request():
    client, calls = make_client(ok({"lesson": {**LESSON, "confidence": 0.8}}))
    out = client.feedback("mem-1", "helpful")
    request = calls[0]
    assert (request.method, request.url.path) == ("POST", "/memory/feedback")
    assert request.headers["x-run-id"] == RUN_ID
    assert body_of(request) == {"memory_id": "mem-1", "kind": "helpful"}
    assert out.applied and out.lesson.confidence == 0.8


def test_feedback_rejects_unknown_kind_before_calling():
    client, calls = make_client(ok({}))
    with pytest.raises(ValueError):
        client.feedback("mem-1", "great")
    assert calls == []


def test_feedback_unknown_memory_is_not_applied():
    tracer = RecordingTracer()
    error = {"status": "error", "error": {"type": "NotFound", "message": "memory x"}}
    client, _ = make_client(lambda r: httpx.Response(404, json=error), tracer)
    out = client.feedback("x", "wrong")
    assert out.applied is False and "NotFound" in out.error
    assert tracer.events[-1]["status"] == "error"


def test_purge_request():
    tracer = RecordingTracer()
    client, calls = make_client(ok({"purged": 3}), tracer)
    out = client.purge(namespace="lessons", requested_by="demo")
    request = calls[0]
    assert (request.method, request.url.path) == ("POST", "/memory/purge")
    assert request.headers["x-run-id"] == RUN_ID
    assert body_of(request) == {"namespace": "lessons", "requested_by": "demo"}
    assert out.purged == 3
    assert tracer.events[-1]["status"] == "success"


@pytest.mark.parametrize(
    "handler",
    [refuse, time_out, lambda r: httpx.Response(500, json={}), ok({"unexpected": True})],
    ids=["refused", "timeout", "http500", "no-count"],
)
def test_purge_failure_raises_never_reports_success(handler):
    tracer = RecordingTracer()
    client, _ = make_client(handler, tracer)
    with pytest.raises(CognitionError):
        client.purge()
    assert tracer.events[-1]["action"] == "purge"
    assert tracer.events[-1]["status"] == "error"


def test_list_memory_request():
    client, calls = make_client(ok({"lessons": [LESSON]}))
    lessons = client.list_memory(namespace="lessons")
    request = calls[0]
    assert (request.method, request.url.path) == ("GET", "/memory")
    assert request.url.params["namespace"] == "lessons"
    assert request.headers["x-run-id"] == RUN_ID
    assert [lesson.id for lesson in lessons] == ["mem-1"]


def test_list_memory_unreachable_raises():
    client, _ = make_client(refuse)
    with pytest.raises(CognitionUnavailable):
        client.list_memory()


def test_ledger_request():
    entry = {"memoryId": "mem-1", "action": "create", "actor": "cognition", "timestamp": 1}
    client, calls = make_client(ok({"entries": [entry]}))
    entries = client.ledger(memory_id="mem-1")
    request = calls[0]
    assert (request.method, request.url.path) == ("GET", "/ledger")
    assert request.url.params["memory_id"] == "mem-1"
    assert request.headers["x-run-id"] == RUN_ID
    assert entries[0].memory_id == "mem-1" and entries[0].action == "create"


def test_bad_request_is_a_request_error_for_reads():
    error = {"status": "error", "error": {"type": "BadRequest", "message": "bad"}}
    client, _ = make_client(lambda r: httpx.Response(400, json=error))
    with pytest.raises(CognitionRequestError) as info:
        client.ledger()
    assert info.value.status_code == 400 and info.value.error_type == "BadRequest"


def test_run_id_defaults_to_tracer_run_id():
    tracer = RecordingTracer()
    tracer.run_id = "run-from-tracer"
    client, calls = make_client(ok({"lessons": []}), tracer, run_id=None)
    client.list_memory()
    assert calls[0].headers["x-run-id"] == "run-from-tracer"


def test_run_id_is_minted_when_none_is_known():
    client, calls = make_client(ok({"lessons": []}), run_id=None)
    client.list_memory()
    client.list_memory()
    assert calls[0].headers.get("x-run-id")
    assert calls[1].headers["x-run-id"] == calls[0].headers["x-run-id"] == client.run_id


def test_no_tracer_is_a_noop():
    client, _ = make_client(refuse, tracer=None)
    assert client.recall("why?").available is False


# -- contract-level integration: runs only when a real cognition service is reachable ---------


def _live_url() -> str | None:
    url = os.getenv("COGNITION_URL")
    if not url:
        return None
    try:
        httpx.get(f"{url.rstrip('/')}/health", timeout=1.0).raise_for_status()
    except httpx.HTTPError:
        return None
    return url


@pytest.mark.skipif(_live_url() is None, reason="COGNITION_URL not set or not reachable")
def test_live_round_trip_in_isolated_namespace():
    namespace = f"pytest-{uuid.uuid4().hex[:8]}"
    lesson = (
        "Restated revenue between 10-K filings: check 8-K item 2.01 for a disposition, "
        "then reconcile against the spun-off entity's companyfacts revenue"
    )
    with CognitionClient(CognitionConfig.from_env(), run_id=f"run-{namespace}") as client:
        assert client.health().available
        stored = client.remember(lesson, namespace=namespace, agent_id="pytest")
        assert stored.stored and stored.lesson is not None
        recalled = client.recall(
            "why was GE revenue restated between filings after a spin-off?", namespace=namespace
        )
        assert stored.lesson.id in recalled.memory_ids
        assert client.feedback(stored.lesson.id, "helpful").applied
        assert client.purge(namespace=namespace, requested_by="pytest").purged == 1
        assert client.recall("restated revenue spin-off", namespace=namespace).lessons == []
        assert client.list_memory(namespace=namespace) == []
        assert any(e.details.get("event") == "purge" for e in client.ledger())
