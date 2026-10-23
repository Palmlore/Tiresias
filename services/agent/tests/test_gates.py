"""Gates (ADR-0006): one interface, three modes, every decision traced."""

import pytest
from loop_helpers import RecordingTracer

from tiresias_agent.gates import Gate, GateRequest


def _req(**kw):
    base = {
        "gate": "critic_escalation",
        "question": "accept?",
        "options": ("accept", "prune"),
        "proposal": "accept",
        "node_id": "n0.1",
        "requested_by": "critic",
    }
    return GateRequest(**{**base, **kw})


def test_auto_follows_the_proposal_and_traces_mode_and_decider():
    tracer = RecordingTracer()
    d = Gate("auto", tracer=tracer).decide(_req())
    assert (d.choice, d.mode, d.decided_by) == ("accept", "auto", "policy:critic_escalation")
    (line,) = tracer.events
    assert line["gate"] == "critic_escalation" and line["gate_mode"] == "auto"
    assert line["decided_by"] == "policy:critic_escalation" and line["node_id"] == "n0.1"


def test_auto_uses_a_gate_specific_policy():
    gate = Gate("auto", tracer=RecordingTracer(), policies={"source": lambda r: ("deny", "cap")})
    d = gate.decide(_req(gate="source", options=("allow", "deny"), proposal="allow"))
    assert d.choice == "deny"


def test_scripted_decision_by_node_then_by_gate():
    tracer = RecordingTracer()
    gate = Gate("scripted", tracer=tracer, scripted={"critic_escalation:n0.1": "prune"})
    assert gate.decide(_req()).choice == "prune"
    gate = Gate("scripted", tracer=tracer, scripted={"critic_escalation": "prune"})
    d = gate.decide(_req(node_id="n9"))
    assert (d.choice, d.decided_by) == ("prune", "script")


def test_scripted_invalid_entry_falls_back_to_policy():
    d = Gate("scripted", tracer=RecordingTracer(), scripted={"critic_escalation": "yolo"}).decide(
        _req()
    )
    assert d.choice == "accept" and d.decided_by.startswith("policy")
    assert "no valid scripted decision" in d.reason


def test_interactive_asks_and_falls_back_without_a_channel():
    d = Gate("interactive", tracer=RecordingTracer(), ask=lambda r: "prune").decide(_req())
    assert (d.choice, d.decided_by) == ("prune", "human")
    d = Gate("interactive", tracer=RecordingTracer()).decide(_req())
    assert d.decided_by.startswith("policy") and "no interactive channel" in d.reason


def test_invalid_mode_or_proposal_is_refused():
    with pytest.raises(ValueError):
        Gate("yolo", tracer=RecordingTracer())
    with pytest.raises(ValueError):
        Gate("auto", tracer=RecordingTracer()).decide(_req(proposal="maybe"))
