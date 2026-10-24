"""Interactive gates over HTTP (gates.GateChannel, runs.py, app.py; ADR-0006).

A run with hitl_mode "interactive" waits at each gate on its own thread: the trace (and SSE)
carries a gate/waiting line, GET /runs/{id}/gates lists it, POST /runs/{id}/gates/{gate_id}
answers it and the run resumes with that choice. No answer by GATE_TIMEOUT_SECONDS (never past
the run budget left) applies the policy's proposal (decided_by "timeout"). Sample mode only, no
network: the memory gate uses test_memory's in-process FakeCognition.
"""

import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from loop_helpers import EXAMPLES, RecordingTracer, request
from test_api import _parse_sse
from test_loop_b import InflatedCounterparty
from test_memory import FakeCognition

from tiresias_agent import app as app_module
from tiresias_agent import orchestrator, runs
from tiresias_agent.gates import Answer, Gate, GateChannel, GateRequest
from tiresias_agent.models import GateList, GateWaiting, RunResult
from tiresias_agent.runs import RunRegistry

WAIT = 15.0  # seconds a test waits for a gate or a run (a sample run takes milliseconds)


def _body(prefix: str, **options) -> dict:
    path = next(p for p in EXAMPLES if p.name.startswith(prefix))
    data = json.loads(path.read_text())
    data.setdefault("options", {}).update({"sample_mode": True, **options})
    return data


def _client(monkeypatch, tmp_path, runner=None, **registry) -> TestClient:
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("GATE_TIMEOUT_SECONDS", "30")
    registry = {"max_runs": 10, "max_concurrent": 4, **registry}
    monkeypatch.setattr(app_module, "registry", RunRegistry(runner=runner, **registry))
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch, tmp_path):
    return _client(monkeypatch, tmp_path)


def _start(client, prefix="01", run_id="gate-1", **options) -> str:
    body = _body(prefix, hitl_mode="interactive", **options)
    response = client.post("/runs", json=body, headers={"X-Run-Id": run_id})
    assert response.status_code == 202, response.text
    return run_id


def _waiting(client, run_id: str, gate: str | None = None) -> dict:
    """Poll GET /runs/{id}/gates until a gate (of this name) is waiting."""
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}/gates").json()
        pending = body["waiting_gate"]
        if pending and (gate is None or pending["gate"] == gate):
            return pending
        assert client.get(f"/runs/{run_id}").json()["status"] == "running", "run ended early"
        time.sleep(0.01)
    raise AssertionError(f"no {gate or ''} gate waiting in {run_id}")


def _answer(client, run_id: str, gate_id: str, choice: str, note: str | None = None):
    body = {"choice": choice} if note is None else {"choice": choice, "note": note}
    return client.post(f"/runs/{run_id}/gates/{gate_id}", json=body)


def _finish(run_id: str) -> RunResult:
    record = app_module.registry.get(run_id)
    assert record.wait(WAIT), "run did not finish"
    assert record.error is None, record.error
    return record.result


def _lines(tmp_path, run_id: str) -> list[dict]:
    path = tmp_path / f"trace-{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _gate_lines(lines, action):
    return [e for e in lines if e["agent"] == "gate" and e["action"] == action]


# --- the round trip ---------------------------------------------------------------------------


def test_interactive_run_waits_and_the_answer_resumes_it(client, tmp_path):
    rid = _start(client)
    first = _waiting(client, rid, "critic_escalation")
    GateWaiting.model_validate(first)
    assert first["status"] == "waiting" and first["gate_id"] == "g1"
    assert first["proposal"] in first["options"] and first["node_id"] == "n0.1"
    assert first["requested_by"] == "critic" and first["context"]["claim"]
    assert first["deadline"].endswith("Z") and first["timeout_seconds"] == 30
    summary = client.get(f"/runs/{rid}").json()
    assert summary["status"] == "running" and summary["hitl_mode"] == "interactive"
    assert summary["waiting_gate"]["gate_id"] == "g1"

    answered = _answer(client, rid, "g1", first["proposal"], note="looks right")
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "answered" and answered.json()["decided_by"] == "human"
    source = _waiting(client, rid, "source")
    assert source["gate_id"] == "g2" and source["context"]["cik"]
    assert _answer(client, rid, "g2", "register").status_code == 200

    result = _finish(rid)
    assert result.end_state == "actionable" and result.stats.gate_decisions == 2
    assert client.get(f"/runs/{rid}").json()["waiting_gate"] is None
    listed = GateList.model_validate(client.get(f"/runs/{rid}/gates").json())
    assert [(g.gate_id, g.status, g.decided_by) for g in listed.gates] == [
        ("g1", "answered", "human"),
        ("g2", "answered", "human"),
    ]
    lines = _lines(tmp_path, rid)
    decides = _gate_lines(lines, "decide")
    assert [(d["gate"], d["gate_mode"], d["decided_by"], d["gate_id"]) for d in decides] == [
        ("critic_escalation", "interactive", "human", "g1"),
        ("source", "interactive", "human", "g2"),
    ]
    assert decides[0]["choice"] == first["proposal"] and decides[0]["note"] == "looks right"
    waiting = _gate_lines(lines, "waiting")
    assert [w["gate_id"] for w in waiting] == ["g1", "g2"]
    assert all(w["status"] == "success" and w["deadline"] and w["options"] for w in waiting)
    answers = _gate_lines(lines, "answered")
    assert [(a["gate_id"], a["decided_by"], a["choice"]) for a in answers] == [
        ("g1", "human", first["proposal"]),
        ("g2", "human", "register"),
    ]
    order = [(e["action"], e.get("gate_id")) for e in lines if e["agent"] == "gate"]
    assert order.index(("waiting", "g1")) < order.index(("answered", "g1"))
    assert order.index(("answered", "g1")) < order.index(("decide", "g1"))


def test_deny_at_the_source_gate_keeps_the_frontier(client):
    rid = _start(client)
    _answer(client, rid, _waiting(client, rid, "critic_escalation")["gate_id"], "frontier")
    gate = _waiting(client, rid, "source")
    assert gate["proposal"] == "register"
    assert _answer(client, rid, gate["gate_id"], "deny", note="not this one").status_code == 200
    result = _finish(rid)
    assert result.end_state == "evidence-frontier" and result.stats.sources_onboarded == 0
    assert "denied" in result.information_need.reason
    assert "interactive, human" in result.information_need.reason


def _inflated_runner(req, *, tracer, run_id, logs_dir, ask=None):
    asked = (req.entity.ticker or "").lower()
    return orchestrator.run_investigation(
        req,
        source=InflatedCounterparty(asked, 6.0),
        tracer=tracer,
        run_id=run_id,
        logs_dir=logs_dir,
        ask=ask,
    )


def test_critic_escalation_answered_accept_capped_takes_the_human_choice(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, runner=_inflated_runner)
    rid = _start(client)
    _answer(client, rid, _waiting(client, rid, "critic_escalation")["gate_id"], "frontier")
    _answer(client, rid, _waiting(client, rid, "source")["gate_id"], "register")
    gate = _waiting(client, rid, "critic_escalation")
    assert gate["gate_id"] == "g3" and gate["proposal"] == "prune"  # beyond the 150% bound
    assert gate["context"]["share"] > 1.5 and gate["context"]["bound"] == 1.5
    assert gate["context"]["entity"] and gate["context"]["residual"] < 0
    assert _answer(client, rid, "g3", "accept-capped").status_code == 200
    result = _finish(rid)
    assert result.end_state == "actionable"  # auto would prune and stop at the frontier
    link = next(n for n in result.why_tree if n.rule == "reconcile")
    assert link.verdict == "accepted" and link.evidence_status == "partially-evidenced"


def test_memory_gate_discard_is_applied(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_MODE", "warm")
    cog = FakeCognition()

    def runner(req, *, tracer, run_id, logs_dir, ask=None):
        return orchestrator.run_investigation(
            req,
            tracer=tracer,
            run_id=run_id,
            logs_dir=logs_dir,
            ask=ask,
            memory_client=cog.client(tracer),
        )

    client = _client(monkeypatch, tmp_path, runner=runner)
    rid = _start(client, memory="warm")
    _answer(client, rid, _waiting(client, rid, "critic_escalation")["gate_id"], "frontier")
    _answer(client, rid, _waiting(client, rid, "source")["gate_id"], "register")
    gate = _waiting(client, rid, "memory")
    assert gate["options"] == ["store", "discard"] and gate["proposal"] == "store"
    assert gate["context"]["tags"]
    assert _answer(client, rid, gate["gate_id"], "discard").status_code == 200
    result = _finish(rid)
    stored = result.memory.stored
    assert stored.stored is False and stored.gate_choice == "discard"
    assert stored.decided_by == "human" and not cog.lessons
    assert result.stats.gate_decisions == 3


# --- timeout ----------------------------------------------------------------------------------


def test_unanswered_gates_timeout_to_the_proposal(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GATE_TIMEOUT_SECONDS", "0.2")
    rid = _start(client)
    result = _finish(rid)
    assert result.end_state == "actionable"  # the proposals are the auto policy's choices
    lines = _lines(tmp_path, rid)
    decides = _gate_lines(lines, "decide")
    assert len(decides) == 2
    assert all(d["decided_by"] == "timeout" and d["choice"] == d["proposal"] for d in decides)
    expired = _gate_lines(lines, "expired")
    assert [e["status"] for e in expired] == ["degraded", "degraded"]
    gates = client.get(f"/runs/{rid}/gates").json()["gates"]
    assert [(g["status"], g["decided_by"]) for g in gates] == [("expired", "timeout")] * 2
    late = _answer(client, rid, "g1", "frontier")
    assert late.status_code == 409 and late.json()["error"]["type"] == "GateExpired"


def test_the_wait_never_goes_past_the_run_budget_left():
    now = [100.0]
    channel = GateChannel(
        tracer=RecordingTracer(), run_deadline=100.05, timeout=60, clock=lambda: now[0]
    )
    start = time.monotonic()
    answer = channel.ask(_req())
    assert time.monotonic() - start < 5
    assert answer.decided_by == "timeout" and channel.list()[0]["timeout_seconds"] == 0.05
    now[0] = 500.0  # budget spent: the next gate does not wait at all
    assert channel.ask(_req()).decided_by == "timeout"
    assert channel.list()[1]["timeout_seconds"] == 0


def test_gate_timeout_env_is_clamped(monkeypatch):
    from tiresias_agent.config import gate_timeout_seconds

    assert gate_timeout_seconds({}) == 120
    assert gate_timeout_seconds({"GATE_TIMEOUT_SECONDS": "0"}) == 0.1
    assert gate_timeout_seconds({"GATE_TIMEOUT_SECONDS": "99999"}) == 1800
    assert gate_timeout_seconds({"GATE_TIMEOUT_SECONDS": "soon"}) == 120


# --- errors -----------------------------------------------------------------------------------


def _error(response, status: int, kind: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["status"] == "error" and body["error"]["type"] == kind
    assert "Traceback" not in response.text


def test_gate_errors_404_409_422(client):
    _error(client.get("/runs/nope/gates"), 404, "RunNotFound")
    _error(_answer(client, "nope", "g1", "register"), 404, "RunNotFound")
    rid = _start(client)
    gate = _waiting(client, rid, "critic_escalation")
    _error(_answer(client, rid, "g9", "frontier"), 404, "GateNotFound")
    bad = _answer(client, rid, gate["gate_id"], "maybe")
    _error(bad, 422, "ValidationError")
    assert "frontier" in bad.json()["error"]["message"]
    _error(client.post(f"/runs/{rid}/gates/g1", json={"note": "no choice"}), 422, "ValidationError")
    assert client.get(f"/runs/{rid}/gates").json()["waiting_gate"]["gate_id"] == "g1"  # still open
    assert _answer(client, rid, "g1", "frontier").status_code == 200
    _error(_answer(client, rid, "g1", "prune"), 409, "GateAlreadyAnswered")
    _answer(client, rid, _waiting(client, rid, "source")["gate_id"], "register")
    _finish(rid)
    auto = client.post("/runs", json=_body("01"), headers={"X-Run-Id": "auto-1"})
    assert auto.status_code == 202
    _finish("auto-1")
    _error(_answer(client, "auto-1", "g1", "register"), 404, "GateNotFound")


# --- concurrency and SSE ----------------------------------------------------------------------


def test_a_waiting_run_blocks_only_itself_not_other_run(client):
    rid = _start(client, run_id="waiting-1")
    _waiting(client, rid, "critic_escalation")
    started = time.monotonic()
    other = client.post("/run", json=_body("03"), headers={"X-Run-Id": "other-1"})
    assert other.status_code == 200 and other.json()["end_state"] == "actionable"
    assert time.monotonic() - started < WAIT
    assert client.get("/health").status_code == 200
    summary = client.get(f"/runs/{rid}").json()
    assert summary["status"] == "running" and summary["waiting_gate"]["gate_id"] == "g1"
    _answer(client, rid, "g1", "frontier")
    _answer(client, rid, _waiting(client, rid, "source")["gate_id"], "register")
    assert _finish(rid).end_state == "actionable"


def test_sse_carries_the_waiting_and_answered_lines(client):
    rid = _start(client, run_id="sse-gate")
    _waiting(client, rid, "critic_escalation")
    record = app_module.registry.get(rid)
    seen: list[dict] = []
    got_waiting = threading.Event()

    def follow():  # the live stream a UI reads, while the run waits
        for chunk in app_module._sse(record):
            if chunk.startswith("event: trace"):
                seen.append(json.loads(chunk.split("data: ", 1)[1]))
                if seen[-1]["action"] == "waiting":
                    got_waiting.set()

    reader = threading.Thread(target=follow, daemon=True)
    reader.start()
    assert got_waiting.wait(WAIT), "the waiting line was not streamed before the answer"
    _answer(client, rid, "g1", "frontier", note="ok")
    _answer(client, rid, _waiting(client, rid, "source")["gate_id"], "register")
    _finish(rid)
    reader.join(WAIT)
    live = [(e["action"], e.get("gate_id")) for e in seen if e["agent"] == "gate"]
    assert ("waiting", "g1") in live and ("answered", "g1") in live and ("decide", "g2") in live
    with client.stream("GET", f"/runs/{rid}/events") as replay:  # and the replay after the run
        events = _parse_sse("".join(replay.iter_text()))
    traces = [json.loads(d) for k, d in events if k == "trace"]
    actions = [(t["action"], t.get("gate_id")) for t in traces if t["agent"] == "gate"]
    assert actions == [
        ("waiting", "g1"),
        ("answered", "g1"),
        ("decide", "g1"),
        ("waiting", "g2"),
        ("answered", "g2"),
        ("decide", "g2"),
    ]
    assert events[-1][0] == "end"


# --- auto and scripted unchanged --------------------------------------------------------------


def _decide_lines(events):
    return [
        {k: v for k, v in e.items() if k not in ("timestamp", "run_id")}
        for e in events
        if e["agent"] == "gate"
    ]


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"hitl_mode": "auto"},
        {"hitl_mode": "scripted", "decisions": {"source": "deny"}},
        {"hitl_mode": "scripted", "decisions": {"critic_escalation:n0.1": "prune"}},
    ],
)
def test_auto_and_scripted_behave_as_before(client, tmp_path, options):
    rid = f"same-{len(options)}-{len(json.dumps(options))}"
    response = client.post("/run", json=_body("01", **options), headers={"X-Run-Id": rid})
    assert response.status_code == 200
    via_api = RunResult.model_validate(response.json())
    direct_req = request("01")
    direct_req.options.sample_mode = True
    for key, value in options.items():
        setattr(direct_req.options, key, value)
    tracer = RecordingTracer()
    direct = orchestrator.run_investigation(direct_req, tracer=tracer)  # no ask, no channel
    assert (via_api.end_state, via_api.stats.gate_decisions) == (
        direct.end_state,
        direct.stats.gate_decisions,
    )
    lines = _lines(tmp_path, rid)
    assert not _gate_lines(lines, "waiting") and not _gate_lines(lines, "answered")
    api_gates = [
        {k: v for k, v in e.items() if k not in ("timestamp", "run_id", "service", "mode")}
        for e in lines
        if e["agent"] == "gate"
    ]
    assert api_gates == _decide_lines(tracer.events)
    assert all("choice" not in e and "gate_id" not in e for e in api_gates)
    summary = client.get(f"/runs/{rid}").json()
    assert summary["waiting_gate"] is None
    assert summary["hitl_mode"] == options.get("hitl_mode", "auto")
    assert client.get(f"/runs/{rid}/gates").json()["gates"] == []


def test_hitl_mode_interactive_from_the_environment_uses_the_channel(client, monkeypatch):
    interactive = runs.settings.__class__(hitl_mode="interactive")  # HITL_MODE=interactive
    monkeypatch.setattr(runs, "settings", interactive)
    monkeypatch.setattr(orchestrator, "settings", interactive)
    body = _body("01")
    del body["options"]["hitl_mode"]  # the example asks for auto; leave it to the environment
    response = client.post("/runs", json=body, headers={"X-Run-Id": "env-1"})
    assert response.status_code == 202
    assert _waiting(client, "env-1", "critic_escalation")["gate_id"] == "g1"
    _answer(client, "env-1", "g1", "frontier")
    _answer(client, "env-1", _waiting(client, "env-1", "source")["gate_id"], "register")
    assert _finish("env-1").end_state == "actionable"


# --- gate unit behaviour ----------------------------------------------------------------------


def _req(**kw) -> GateRequest:
    base = {
        "gate": "critic_escalation",
        "question": "accept?",
        "options": ("accept", "prune"),
        "proposal": "accept",
        "node_id": "n0.1",
        "requested_by": "critic",
    }
    return GateRequest(**{**base, **kw})


def test_gate_applies_the_policy_on_timeout_and_keeps_the_note_of_an_answer():
    tracer = RecordingTracer()
    gate = Gate("interactive", tracer=tracer, ask=lambda r: Answer(None, decided_by="timeout"))
    d = gate.decide(_req())
    assert (d.choice, d.decided_by) == ("accept", "timeout") and "deadline" in d.reason
    gate = Gate("interactive", tracer=tracer, ask=lambda r: Answer("prune", note="n", gate_id="g1"))
    d = gate.decide(_req())
    assert (d.choice, d.decided_by, d.note, d.gate_id) == ("prune", "human", "n", "g1")
    line = tracer.events[-1]
    assert (line["choice"], line["note"], line["gate_id"]) == ("prune", "n", "g1")


def test_gate_context_and_note_carry_no_secrets(monkeypatch):
    secret = "s3cr3t-value-0123456789"
    monkeypatch.setenv("ZETARIS_TOKEN", secret)
    tracer = RecordingTracer()
    channel = GateChannel(tracer=tracer, timeout=5)
    context = {
        "sql": f"SELECT 1 -- {secret}",
        "api_key": "abc",
        "headers": {"Authorization": "Bearer xyz.abc", "x": ["Bearer qqq-www"]},
        "long": "x" * 5000,
        "nested": {"a": {"b": {"c": {"d": secret}}}},
        "share": 1.2,
    }
    done = threading.Event()

    def run():
        channel.ask(_req(context=context))
        done.set()

    threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + WAIT
    while channel.waiting() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    channel.answer("g1", "prune", note=f"token {secret} Bearer abc.def")
    assert done.wait(WAIT)
    dumped = json.dumps([channel.list(), tracer.events])
    for leaked in (secret, "xyz.abc", "qqq-www", "abc.def", '"abc"'):
        assert leaked not in dumped
    shown = channel.list()[0]
    assert shown["context"]["share"] == 1.2 and len(shown["context"]["long"]) == 300
    assert shown["context"]["api_key"] == "***"


def test_end_to_end_trace_of_an_interactive_run_has_no_env_secret(client, tmp_path, monkeypatch):
    secret = "tok-0123456789-abcdef"
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", secret)
    rid = _start(client, run_id="secret-1")
    _answer(client, rid, _waiting(client, rid)["gate_id"], "frontier", note=f"pasted {secret}")
    _answer(client, rid, _waiting(client, rid, "source")["gate_id"], "register")
    _finish(rid)
    assert secret not in (tmp_path / "trace-secret-1.jsonl").read_text()
    assert secret not in json.dumps(client.get(f"/runs/{rid}/gates").json())


PROBE = "tok-0123456789-abcdef"  # DuelLoop round 5: SECRET_PROBE in the environment


def _probe_runner(req, *, tracer, run_id, logs_dir, ask=None):
    """A gate whose question, options and proposal echo the request question (as a pasted
    secret would reach a gate), then the normal investigation."""
    gate = Gate("interactive", tracer=tracer, ask=ask)
    secret_option = f"use {PROBE}"
    decision = gate.decide(
        GateRequest(
            gate="framing",
            question=f"Confirm the question: {req.question}",
            options=(secret_option, "Bearer abcdef-ghijkl", "plain"),
            proposal=secret_option,
            node_id="n0",
            requested_by=f"framer {PROBE}",
            context={"question": req.question},
        )
    )
    assert decision.choice in (secret_option, "Bearer abcdef-ghijkl", "plain")
    return orchestrator.run_investigation(
        req, tracer=tracer, run_id=run_id, logs_dir=logs_dir, ask=ask
    )


def test_probe_env_secret_in_the_question_never_reaches_gates_sse_or_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_PROBE", PROBE)
    client = _client(monkeypatch, tmp_path, runner=_probe_runner)
    body = _body("01", hitl_mode="interactive")
    body["question"] = f"Why did revenue fall? my key is {PROBE} and Bearer zzz-yyy-xxx"
    assert client.post("/runs", json=body, headers={"X-Run-Id": "probe-1"}).status_code == 202
    gate = _waiting(client, "probe-1", "framing")
    assert PROBE not in json.dumps(gate) and "zzz-yyy-xxx" not in json.dumps(gate)
    assert "***" in gate["question"] and gate["options"][0] == "use ***"
    assert gate["proposal"] == "use ***" and gate["options"][1] == "***"
    assert gate["requested_by"] == "framer ***"
    bad = _answer(client, "probe-1", gate["gate_id"], f"use {PROBE}")  # the raw one is no option
    _error(bad, 422, "ValidationError")
    assert PROBE not in bad.text
    ok = _answer(client, "probe-1", gate["gate_id"], "use ***", note=f"see {PROBE}")
    assert ok.status_code == 200 and ok.json()["note"] == "see ***"
    _answer(
        client, "probe-1", _waiting(client, "probe-1", "critic_escalation")["gate_id"], "frontier"
    )
    _answer(client, "probe-1", _waiting(client, "probe-1", "source")["gate_id"], "register")
    assert _finish("probe-1").end_state == "actionable"
    gates = client.get("/runs/probe-1/gates").json()
    assert PROBE not in json.dumps(gates) and gates["gates"][0]["choice"] == "use ***"
    text = (tmp_path / "trace-probe-1.jsonl").read_text()
    assert PROBE not in text and "zzz-yyy-xxx" not in text
    lines = _lines(tmp_path, "probe-1")
    framing = [e for e in lines if e["agent"] == "gate" and e.get("gate") == "framing"]
    assert [e["action"] for e in framing] == ["waiting", "answered", "decide"]
    assert framing[-1]["decided_by"] == "human" and framing[-1]["choice"] == "use ***"
    with client.stream("GET", "/runs/probe-1/events") as stream:
        sse = "".join(stream.iter_text())
    assert PROBE not in sse and "zzz-yyy-xxx" not in sse and '"action": "waiting"' in sse
    # Outside the gates too: run_accepted, the orchestrator start and the framer echo the
    # question; the tracer masks env secrets and bearer tokens on every line.
    echoed = [e for e in lines if e["action"] in ("run_accepted", "start", "frame")]
    assert echoed and all("***" in e.get("input_summary", "***") for e in echoed)


def test_secret_option_maps_back_to_the_agents_option():
    secret = "opt-secret-0123456789"
    channel = GateChannel(tracer=RecordingTracer(), timeout=5)
    got = []
    req = _req(options=(f"keep {secret}", "prune"), proposal=f"keep {secret}", question=secret)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPTION_TOKEN", secret)
        thread = threading.Thread(target=lambda: got.append(channel.ask(req)), daemon=True)
        thread.start()
        deadline = time.monotonic() + WAIT
        while channel.waiting() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        shown = channel.waiting()
        assert shown["options"] == ["keep ***", "prune"] and shown["question"] == "***"
        channel.answer(shown["gate_id"], "keep ***", note=f"{secret} Bearer abc-def-ghi")
        thread.join(WAIT)
        assert got[0].choice == f"keep {secret}"  # the run gets the real option
        assert channel.list()[0]["note"] == "*** Bearer ***"


def test_the_tracer_masks_env_secret_values_on_every_line(tmp_path, monkeypatch, capsys):
    from tiresias_agent.trace import Tracer

    monkeypatch.setenv("ANY_API_KEY", "value-0123456789")
    tracer = Tracer("mask-1", logs_dir=tmp_path)
    tracer.emit(agent="orchestrator", action="start", input_summary="q value-0123456789 x")
    line = (tmp_path / "trace-mask-1.jsonl").read_text()
    assert "value-0123456789" not in line and "q *** x" in line
    assert "value-0123456789" not in capsys.readouterr().out


# --- contract ---------------------------------------------------------------------------------


def test_gate_contract_matches_the_models_and_the_summary(client):
    yaml = pytest.importorskip("yaml")
    from tiresias_agent import models

    spec = yaml.safe_load((EXAMPLES[0].parents[1] / "contracts/openapi/agent.yaml").read_text())
    schemas = spec["components"]["schemas"]
    for name in ("GateAnswer", "GateWaiting", "GateList"):
        assert set(schemas[name]["properties"]) == set(getattr(models, name).model_fields), name
    assert schemas["GateWaiting"]["properties"]["gate"]["enum"] == list(models.GateName.__args__)
    record = runs.RunRecord(run_id="c", max_lines=10)
    assert set(schemas["RunStatus"]["properties"]) == set(record.summary())
    gates = spec["paths"]["/runs/{run_id}/gates/{gate_id}"]["post"]["responses"]
    assert set(gates) == {"200", "404", "409", "422"}
    assert "get" in spec["paths"]["/runs/{run_id}/gates"]


# --- CLI --------------------------------------------------------------------------------------


def test_cli_interactive_reads_answers_on_stdin(tmp_path, capsys, monkeypatch):
    example = next(p for p in EXAMPLES if p.name.startswith("01"))
    monkeypatch.setattr("sys.stdin", io.StringIO("\ndeny\n"))  # Enter = proposal, then deny
    code = orchestrator.main([str(example), "--interactive", "--logs-dir", str(tmp_path)])
    out = capsys.readouterr()
    result = RunResult.model_validate_json(out.out)
    assert code == 0 and result.end_state == "evidence-frontier"
    assert "denied" in result.information_need.reason
    assert "[gate source]" in out.err and "Enter = register" in out.err


def test_cli_interactive_at_end_of_input_falls_back_to_the_policy(tmp_path, capsys, monkeypatch):
    example = next(p for p in EXAMPLES if p.name.startswith("01"))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert orchestrator.main([str(example), "--interactive", "--logs-dir", str(tmp_path)]) == 0
    assert RunResult.model_validate_json(capsys.readouterr().out).end_state == "actionable"
