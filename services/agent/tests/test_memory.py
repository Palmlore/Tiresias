"""H-MEM memory in the investigation (agents/memory.py, ADR-0005, causal-criteria.md §6).

Cognition is faked over httpx.MockTransport (FakeCognition: the server's routes and body shapes,
contracts/openapi/cognition.yaml), so no test needs a network; the last test runs the same loop
against a real cognition service when COGNITION_URL answers (skipped otherwise).
"""

import json
import os
import re
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from loop_helpers import RecordingTracer, request
from test_orchestrator import FORBIDDEN  # the case-literal guard's patterns

from tiresias_agent import app as app_module
from tiresias_agent.adapters.cognition import CognitionClient, CognitionConfig
from tiresias_agent.agents import hypothesiser, memory
from tiresias_agent.agents.common import Frame, Node
from tiresias_agent.agents.critic import judge_lesson
from tiresias_agent.orchestrator import run_investigation, run_loop_a

SUCCESS = memory.SUCCESS_STATES


class FakeCognition:
    """The cognition routes the agent uses, in memory. Recall returns every lesson of the
    namespace at a fixed relevance (the real server filters by RECALL_THRESHOLD; tested last)."""

    def __init__(self, relevance: float = 0.3) -> None:
        self.lessons: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.relevance = relevance
        self.seq = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        path = req.url.path
        self.calls.append((req.method, path, body))
        ns = body.get("namespace") or req.url.params.get("namespace") or "lessons"
        if path == "/memory/recall":
            found = [
                {**lsn, "relevance": self.relevance}
                for lsn in self.lessons.values()
                if lsn["namespace"] == ns
            ]
            return httpx.Response(200, json={"lessons": found, "context": "", "trace": {}})
        if path == "/memory/remember":
            gate = body.get("gate")
            if gate and gate.get("decision") == "reject":
                return httpx.Response(200, json={"stored": False, "gate": gate})
            self.seq += 1
            lesson = {
                "id": f"mem-{self.seq}",
                "content": body["content"],
                "namespace": ns,
                "confidence": body.get("confidence", 0.7),
                "tags": [*body.get("tags", []), "lesson", f"agent:{body.get('agent_id')}"],
                "runId": req.headers.get("x-run-id"),
            }
            self.lessons[lesson["id"]] = lesson
            return httpx.Response(200, json={"stored": True, "lesson": lesson, "gate": gate})
        if path == "/memory/feedback":
            lesson = self.lessons.get(body["memory_id"])
            if lesson is None:
                return httpx.Response(404, json={"status": "error", "error": {"type": "NotFound"}})
            delta = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.2}[body["kind"]]
            lesson["confidence"] = round(lesson["confidence"] + delta, 4)
            return httpx.Response(200, json={"lesson": lesson})
        if path == "/memory" and req.method == "GET":
            found = [lsn for lsn in self.lessons.values() if lsn["namespace"] == ns]
            return httpx.Response(200, json={"lessons": found})
        if path == "/memory/purge":
            target = body.get("namespace") or req.url.params.get("namespace")
            gone = [
                k for k, v in self.lessons.items() if target is None or v["namespace"] == target
            ]
            for k in gone:
                del self.lessons[k]
            return httpx.Response(200, json={"purged": len(gone)})
        return httpx.Response(404, json={"status": "error", "error": {"type": "NotFound"}})

    def client(self, tracer=None) -> CognitionClient:
        config = CognitionConfig(url="http://cognition.test", timeout_s=1.0, retries=0)
        return CognitionClient(
            config, run_id="run-mem", tracer=tracer, transport=httpx.MockTransport(self.handler)
        )

    def paths(self) -> list[str]:
        return [p for _, p, _ in self.calls]


@pytest.fixture
def warm(monkeypatch):
    monkeypatch.setenv("MEMORY_MODE", "warm")


def _run(prefix, cog, *, tracer=None, mode="warm", tmp_path=None, **opts):
    req = request(prefix)
    req.options.memory = mode
    req.options.sample_mode = True
    for k, v in opts.items():
        setattr(req.options, k, v)
    tracer = tracer or RecordingTracer()
    client = cog.client(tracer) if cog is not None else None
    return run_investigation(req, tracer=tracer, memory_client=client)


def _cold(prefix):
    req = request(prefix)
    req.options.memory = "cold"
    return run_investigation(req, tracer=RecordingTracer())


def _shape(result):
    return [(n.node_id, n.verdict, n.rule, n.label, n.share_explained) for n in result.why_tree]


def _chosen_path(result):
    return [
        (n.node_id, n.verdict, n.rule)
        for n in result.why_tree
        if n.verdict in ("accepted", "frontier", "root")
    ]


# --- write-back: lessons, never answers ---------------------------------------------------------


@pytest.mark.parametrize("prefix", ["01", "02", "03", "04"])
def test_lesson_has_no_entity_literals(warm, prefix):
    cog = FakeCognition()
    result = _run(prefix, cog)
    stored = result.memory.stored
    assert stored and stored.stored and stored.memory_id == "mem-1"
    text = stored.content + " " + " ".join(stored.tags)
    hits = [p for p in FORBIDDEN if re.search(p, text)]
    assert not hits, hits
    assert "$" not in text and not re.search(r"\d{4,}", text)
    assert result.effect.entity.lower() not in text.lower().split()
    assert any(t.startswith("path:") for t in stored.tags)
    # What reaches cognition is exactly that generic lesson.
    remember = [b for _, p, b in cog.calls if p == "/memory/remember"]
    assert remember[0]["content"] == stored.content and remember[0]["agent_id"] == "memory"


def test_entity_free_check_catches_names_numbers_and_amounts():
    literals = ["acme", "0000012345", "Widget Spinco Inc."]
    assert memory.entity_free("reconcile against that registrant", literals) is None
    assert memory.entity_free("Revenue of ACME fell", literals) == "contains an entity literal"
    assert memory.entity_free("from Widget Spinco Inc. filings", literals)
    assert memory.entity_free("CIK 12345 filed", literals)
    assert memory.entity_free("it moved by $8.20B", literals) == "contains an amount"


def test_memory_gate_stores_by_policy_and_is_traced(warm):
    cog, tracer = FakeCognition(), RecordingTracer()
    result = _run("04", cog, tracer=tracer)
    gate = [e for e in tracer.events if e["agent"] == "gate" and e.get("gate") == "memory"]
    assert len(gate) == 1 and gate[0]["output_summary"].startswith("store")
    body = next(b for _, p, b in cog.calls if p == "/memory/remember")
    assert body["gate"] == {
        "mode": "auto",
        "decision": "approve",
        "decidedBy": "policy:memory",
        "reason": "auto policy: follow the proposal of memory",
    }
    line = next(e for e in tracer.events if e["agent"] == "memory" and e["action"] == "remember")
    assert line["status"] == "success" and line["memory_ids"] == ["mem-1"]
    assert result.memory.stored.gate_choice == "store"
    assert result.stats.gate_decisions == _cold("04").stats.gate_decisions + 1


def test_memory_gate_discard_is_sent_as_a_reject_and_not_stored(warm):
    cog = FakeCognition()
    result = _run("04", cog, hitl_mode="scripted", decisions={"memory": "discard"})
    body = next(b for _, p, b in cog.calls if p == "/memory/remember")
    assert body["gate"]["decision"] == "reject" and body["gate"]["decidedBy"] == "script"
    assert result.memory.stored.stored is False and result.memory.stored.gate_choice == "discard"
    assert cog.lessons == {}


def test_same_lesson_is_not_stored_twice(warm):
    cog = FakeCognition()
    _run("04", cog)
    again = _run("02", cog)
    assert again.memory.stored.stored is False
    assert again.memory.stored.memory_id == "mem-1"
    assert list(cog.lessons) == ["mem-1"]


def test_no_lesson_from_a_run_that_did_not_reach_a_cause(warm, monkeypatch):
    monkeypatch.setenv("MAX_NEW_SOURCES_PER_RUN", "0")  # Loop B capped: end state budget
    cog = FakeCognition()
    result = _run("01", cog)
    assert result.end_state == "budget"
    assert "/memory/remember" not in cog.paths() and result.memory.stored is None


# --- recall: reorder only, fewer hops on a similar case --------------------------------------


def test_warm_recall_tests_the_lesson_first_and_saves_hops(warm):
    cog = FakeCognition()
    first = _run("04", cog)
    tracer = RecordingTracer()
    warm_run = _run("01", cog, tracer=tracer)
    cold = _cold("01")
    assert first.memory.recalled == []  # empty memory: the first run is cold in effect
    assert warm_run.end_state == cold.end_state == "actionable"
    assert warm_run.stats.hops < cold.stats.hops
    assert warm_run.stats.queries < cold.stats.queries
    assert (warm_run.root_cause, warm_run.action) == (cold.root_cause, cold.action)
    assert _chosen_path(warm_run) == _chosen_path(cold)
    rec = warm_run.memory.recalled[0]
    assert rec.memory_id == "mem-1" and rec.status == "supported"
    assert [s.node_id for s in warm_run.memory.skipped] == ["n0.2", "n0.3"]
    propose = [
        e for e in tracer.events if e["agent"] == "hypothesiser" and e["action"] == "propose"
    ]
    assert propose[0]["node_id"] == "n0.1" and propose[0]["memory_ids"] == ["mem-1"]
    recall = next(e for e in tracer.events if e["agent"] == "memory" and e["action"] == "recall")
    assert recall["memory_ids"] == ["mem-1"] and recall["scores"] == {"mem-1": 0.3}
    assert ("mem-1", "helpful") in [(f.memory_id, f.kind) for f in warm_run.memory.feedback]


def test_recall_query_is_generic():
    req = request("01")
    from tiresias_agent.adapters.zetaris import make_source
    from tiresias_agent.agents.framer import frame

    f = frame(req, make_source(True, RecordingTracer()), RecordingTracer())
    q = memory.recall_query(f)
    assert "restated" in q and "revenue" in q
    assert not [p for p in FORBIDDEN if re.search(p, q)] and not re.search(r"\d", q)


def test_hypothesiser_preference_reorders_but_keeps_node_ids():
    f = _frame("03")
    root = Node("n0", None, 0, f.metric, "root", "breakdown", "root", effect=f.delta)
    plain = hypothesiser.propose(f, root, {f.metric}, max_branches=3, tracer=RecordingTracer())
    prefer = [("component", "IncomeLossFromDiscontinuedOperationsNetOfTax", "mem-9")]
    pref = hypothesiser.propose(
        f, root, {f.metric}, max_branches=3, tracer=RecordingTracer(), prefer=prefer
    )
    assert {c.node_id: c.variable for c in plain} == {c.node_id: c.variable for c in pref}
    assert pref[0].variable == "IncomeLossFromDiscontinuedOperationsNetOfTax"
    assert pref[0].node_id == "n0.2" and pref[0].lesson == "mem-9"
    assert all(c.lesson is None for c in pref[1:])


def _frame(prefix) -> Frame:
    from tiresias_agent.adapters.zetaris import make_source
    from tiresias_agent.agents.framer import frame

    return frame(request(prefix), make_source(True, RecordingTracer()), RecordingTracer())


# --- the critic rejects a lesson that does not fit ---------------------------------------------


def test_misapplied_spin_off_lesson_is_rejected_with_negative_feedback(warm):
    cog = FakeCognition()
    _run("04", cog)  # the spin-off lesson
    tracer = RecordingTracer()
    result = _run("03", cog, tracer=tracer)
    cold = _cold("03")
    assert result.memory.rejected == ["mem-1"]
    rec = result.memory.recalled[0]
    assert rec.status == "rejected" and rec.node_ids == ["n0.2"]
    feedback = [b for _, p, b in cog.calls if p == "/memory/feedback"]
    assert feedback == [{"memory_id": "mem-1", "kind": "not_helpful"}]
    critic = [e for e in tracer.events if e["agent"] == "critic" and e["action"] == "lesson_reject"]
    assert critic and critic[0]["memory_ids"] == ["mem-1"] and critic[0]["status"] == "rejected"
    # The lesson changed nothing but the test order: same tree, end state, action, hops.
    assert _shape(result) == _shape(cold)
    assert (result.end_state, result.action) == (cold.end_state, cold.action)
    assert result.stats.hops == cold.stats.hops and result.memory.skipped == []
    # The impairment run then stores its own lesson.
    assert result.memory.stored.stored and result.memory.stored.memory_id == "mem-2"


def test_judge_lesson_rules():
    node = Node("n0.2", "n0", 1, "X", "c", "breakdown", "pruned", rule="component", reason="small")
    v = judge_lesson(node, expected="frontier", exact=False, fits=False)
    assert (v.decision, v.feedback, v.conclusive) == ("reject", "not_helpful", False)
    assert judge_lesson(node, expected="accepted", exact=True, fits=True).feedback == "wrong"
    held = Node("n0.1", "n0", 1, "X", "c", "causal", "frontier", rule="filing_event")
    assert judge_lesson(held, expected="frontier", exact=True, fits=True).conclusive
    assert not judge_lesson(held, expected="frontier", exact=True, fits=False).conclusive
    weak = Node("n0.1", "n0", 1, "X", "c", "breakdown", "accepted", rule="component", raw_share=0.3)
    v = judge_lesson(weak, expected="accepted", exact=True, fits=True)
    assert v.decision == "support" and not v.conclusive


def test_shortcut_that_finds_no_cause_falls_back_to_a_full_loop_a(warm, monkeypatch):
    cog = FakeCognition()
    _run("04", cog)
    monkeypatch.setenv("MAX_NEW_SOURCES_PER_RUN", "0")  # Loop B cannot onboard: no cause
    tracer = RecordingTracer()
    result = _run("01", cog, tracer=tracer)
    cold = _cold("01")
    assert result.end_state == cold.end_state == "budget"
    assert result.memory.fallback is True
    assert ("mem-1", "not_helpful") in [(f.memory_id, f.kind) for f in result.memory.feedback]
    assert _shape(result) == _shape(cold)  # the second pass tested every candidate
    assert result.stats.hops > cold.stats.hops  # both passes are counted
    assert any(e["agent"] == "memory" and e["action"] == "fallback" for e in tracer.events)


# --- modes and degradation --------------------------------------------------------------------


def test_cold_mode_makes_no_memory_call(warm):
    cog = FakeCognition()
    result = _run("01", cog, mode="cold")
    assert cog.calls == []
    assert result.memory.mode == "cold" and result.memory.available is None


def test_memory_mode_off_overrides_the_request(monkeypatch):
    monkeypatch.setenv("MEMORY_MODE", "off")
    cog = FakeCognition()
    result = _run("01", cog, mode="warm")
    assert cog.calls == [] and result.memory.mode == "cold"
    assert "MEMORY_MODE=off" in result.memory.note


def test_memory_mode_env_is_the_default(monkeypatch):
    monkeypatch.setenv("MEMORY_MODE", "cold")
    assert memory.resolve_mode(None)[0] == "cold"
    assert memory.resolve_mode("warm")[0] == "warm"
    monkeypatch.setenv("MEMORY_MODE", "warm")
    assert memory.resolve_mode(None)[0] == "warm"


def test_cognition_down_degrades_with_one_line_and_never_fails(warm):
    def down(req):
        raise httpx.ConnectError("refused", request=req)

    tracer = RecordingTracer()
    config = CognitionConfig(url="http://cognition.test", timeout_s=1.0, retries=0)
    client = CognitionClient(config, run_id="r", tracer=tracer, transport=httpx.MockTransport(down))
    req = request("04")
    req.options.memory = "warm"
    result = run_investigation(req, tracer=tracer, memory_client=client)
    cold = _cold("04")
    degraded = [e for e in tracer.events if e["status"] == "degraded"]
    assert len(degraded) == 1 and degraded[0]["action"] == "recall"
    assert result.memory.available is False and result.memory.stored is None
    assert _shape(result) == _shape(cold) and result.end_state == cold.end_state


@pytest.mark.parametrize("prefix", ["01", "02", "03", "04"])
def test_examples_keep_end_state_and_tree_with_empty_memory(warm, prefix):
    result = _run(prefix, FakeCognition())
    cold = _cold(prefix)
    assert result.end_state == cold.end_state and _shape(result) == _shape(cold)


@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_examples_keep_end_state_and_chosen_path_after_a_lesson(warm, prefix):
    cog = FakeCognition()
    _run("04", cog)
    result = _run(prefix, cog)
    cold = _cold(prefix)
    assert (result.end_state, result.root_cause, result.action) == (
        cold.end_state,
        cold.root_cause,
        cold.action,
    )
    assert _chosen_path(result) == _chosen_path(cold)
    tested = {n.node_id: (n.verdict, n.share_explained) for n in result.why_tree}
    full = {n.node_id: (n.verdict, n.share_explained) for n in cold.why_tree}
    assert tested.items() <= full.items()  # memory only skips; it never changes a verdict
    skipped = {s.node_id for s in result.memory.skipped}
    assert set(full) - set(tested) == skipped


def test_loop_a_only_runs_have_no_memory(warm):
    assert run_loop_a(request("01"), tracer=RecordingTracer()).memory is None


# --- POST /memory/purge -------------------------------------------------------------------------


def test_purge_endpoint_reports_what_cognition_purged(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    cog = FakeCognition()
    cog.lessons["mem-1"] = {
        "id": "mem-1",
        "content": "x",
        "namespace": "lessons",
        "confidence": 0.7,
        "tags": [],
    }
    monkeypatch.setattr(app_module, "memory_client", lambda run_id, tracer: cog.client(tracer))
    api = TestClient(app_module.app)
    response = api.post("/memory/purge", headers={"X-Run-Id": "purge-1"})
    assert response.status_code == 200
    assert response.json() == {
        "status": "purged",
        "purged": 1,
        "namespace": None,
        "requested_by": "api",
    }
    assert cog.lessons == {} and cog.calls[-1][2] == {"requested_by": "api"}
    lines = [json.loads(x) for x in (tmp_path / "trace-purge-1.jsonl").read_text().splitlines()]
    audit = [x for x in lines if x["agent"] == "api" and x["action"] == "memory_purge"]
    assert len(audit) == 1 and audit[0]["purged"] == 1 and audit[0]["status"] == "success"
    again = api.post("/memory/purge?namespace=lessons", json={"requested_by": "demo"})
    assert again.json()["purged"] == 0 and again.json()["namespace"] == "lessons"


@pytest.mark.parametrize(
    "reply, status, error_type",
    [
        (None, 503, "MemoryUnavailable"),
        (httpx.Response(200, json={"ok": True}), 503, "MemoryUnavailable"),
        (
            httpx.Response(400, json={"status": "error", "error": {"type": "BadRequest"}}),
            502,
            "MemoryError",
        ),
    ],
)
def test_purge_endpoint_never_claims_a_purge_that_did_not_happen(
    monkeypatch, tmp_path, reply, status, error_type
):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))

    def handler(req):
        if reply is None:
            raise httpx.ConnectError("refused", request=req)
        return reply

    def factory(run_id, tracer):
        config = CognitionConfig(url="http://cognition.test", timeout_s=1.0, retries=0)
        return CognitionClient(
            config, run_id=run_id, tracer=tracer, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(app_module, "memory_client", factory)
    response = TestClient(app_module.app).post("/memory/purge", headers={"X-Run-Id": "p-2"})
    assert response.status_code == status
    body = response.json()
    assert body["status"] == "error" and body["error"]["type"] == error_type
    assert "purged" not in body
    lines = (tmp_path / "trace-p-2.jsonl").read_text()
    assert '"action": "memory_purge"' in lines and "purge NOT done" in lines


# --- contract ------------------------------------------------------------------------------------


def test_memory_contract_matches_the_models():
    yaml = pytest.importorskip("yaml")
    from test_models import CONTRACT

    from tiresias_agent import models

    spec = yaml.safe_load(CONTRACT.read_text())
    schemas = spec["components"]["schemas"]
    for name in (
        "RunMemory",
        "RecalledLesson",
        "SkippedCandidate",
        "MemoryFeedback",
        "StoredLesson",
        "PurgeRequest",
        "PurgeResult",
    ):
        assert set(schemas[name]["properties"]) == set(getattr(models, name).model_fields), name
    assert "memory" in schemas["RunResult"]["properties"]
    purge = spec["paths"]["/memory/purge"]["post"]
    assert "implemented" in purge["summary"] and {"200", "502", "503"} <= set(purge["responses"])
    phases = spec["paths"]["/runs/{run_id}/events"]["get"]["description"]
    assert "recall" in phases and "remember" in phases


# --- against a real cognition service (skipped without one) ----------------------------------


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
def test_memory_loop_against_the_real_cognition_service(monkeypatch):
    namespace = f"pytest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MEMORY_MODE", "warm")
    monkeypatch.setenv("MEMORY_NAMESPACE", namespace)

    def run(prefix):
        req = request(prefix)
        req.options.memory, req.options.sample_mode = "warm", True
        with CognitionClient(CognitionConfig.from_env()) as client:
            return run_investigation(req, tracer=RecordingTracer(), memory_client=client)

    try:
        a = run("04")
        assert a.memory.available and a.memory.stored.stored
        b = run("01")  # recalled above RECALL_THRESHOLD with the real scorer
        assert b.memory.recalled and b.memory.recalled[0].status == "supported"
        assert b.stats.hops < _cold("01").stats.hops
        c = run("03")
        assert a.memory.stored.memory_id in c.memory.rejected
    finally:
        with CognitionClient(CognitionConfig.from_env()) as client:
            assert client.purge(namespace=namespace, requested_by="pytest").purged >= 1
