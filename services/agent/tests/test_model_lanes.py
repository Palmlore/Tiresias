"""Model lanes (ADR-0010) against a fake Nightshift Runtime (httpx.MockTransport); no network.

The model proposes, the rules and the data decide: proposals are validated against the queryable
vocabulary and tested by the analyst and critic; the second opinion never overrides the rule
verdict on the auto policy; an outage, a timeout or the cap leaves the rule-only result intact.
"""

import json
from pathlib import Path

import httpx
import pytest
from loop_helpers import EXAMPLES, RecordingTracer, request

from tiresias_agent.adapters.nightshift import NightshiftConfig
from tiresias_agent.adapters.zetaris import SampleSource
from tiresias_agent.agents import model_lanes
from tiresias_agent.agents.common import Node
from tiresias_agent.agents.framer import frame as frame_question
from tiresias_agent.agents.hypothesiser import propose as rule_propose
from tiresias_agent.config import LoopBudgets
from tiresias_agent.models import RunResult
from tiresias_agent.orchestrator import run_investigation, run_loop_a
from tiresias_agent.trace import Tracer

TOKEN = "rt-lanes-test-0123456789-SECRET"
CFG = NightshiftConfig(base_url="http://vm.test:8088/v1", token=TOKEN, timeout_seconds=5)
ROUTED = "vendor/test-model-30b"
ON = model_lanes.LaneSwitch(True, "on", "test")


def completion(content, n=[0]):  # noqa: B006 - a counter for distinct receipt ids
    n[0] += 1
    return {
        "id": "chatcmpl-x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "meterless": {
            "receipt_id": f"rcpt_{n[0]}",
            "routed_model": ROUTED,
            "winning_route": {"provider": "vendor", "model": ROUTED},
        },
    }


class FakeNightshift:
    """Answers each lane from a callable: (role, user prompt) -> JSON value."""

    def __init__(self, propose=None, review=None, wording=None):
        self.answers = {"propose": propose, "review": review, "wording": wording}
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        system = body["messages"][1]["content"]
        user = body["messages"][2]["content"]
        role = (
            "propose"
            if system == model_lanes.HYPOTHESISER_SYSTEM
            else "review"
            if system == model_lanes.CRITIC_SYSTEM
            else "wording"
        )
        self.calls.append((role, body["model"]))
        answer = self.answers[role]
        value = answer(user) if callable(answer) else answer
        if value is None:
            value = {}
        text = value if isinstance(value, str) else json.dumps(value)
        return httpx.Response(200, json=completion(text))


def lanes_for(tracer, handler, *, max_calls=12, deadline=None, clock=None):
    kw = {"clock": clock} if clock else {}
    return model_lanes.ModelLanes(
        tracer=tracer,
        switch=ON,
        config=CFG,
        max_calls=max_calls,
        deadline=deadline,
        transport=httpx.MockTransport(handler),
        **kw,
    )


def _shape(result: RunResult):
    return (
        result.end_state,
        [(lk.node_id, lk.verdict, lk.rule, lk.label) for lk in result.why_tree],
    )


def _sample_request(prefix, **opts):
    req = request(prefix)
    req.options.sample_mode = True
    for k, v in opts.items():
        setattr(req.options, k, v)
    return req


@pytest.fixture(scope="module")
def rule_only(tmp_path_factory):
    out = {}
    for path in EXAMPLES:
        prefix = path.name[:2]
        out[prefix] = run_investigation(
            _sample_request(prefix), logs_dir=tmp_path_factory.mktemp(prefix)
        )
    return out


def _frame_03():
    tracer = RecordingTracer()
    frame = frame_question(_sample_request("03"), SampleSource(tracer=tracer), tracer)
    root = Node(
        node_id="n0",
        parent_id=None,
        depth=0,
        variable=frame.metric,
        claim="root",
        label="breakdown",
        verdict="root",
        effect=frame.delta,
    )
    return frame, root


# --- switch -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "env", "sample", "configured", "enabled"),
    [
        (None, {}, True, True, False),  # auto, sample mode: rule lanes
        (None, {}, False, True, True),  # auto, live, configured: on
        (None, {}, False, False, False),  # auto, live, not configured
        (None, {"MODEL_LANES": "on"}, True, True, True),  # on works in sample mode too
        (None, {"MODEL_LANES": "on"}, False, False, False),  # on but not configured
        (None, {"MODEL_LANES": "off"}, False, True, False),
        ("off", {"MODEL_LANES": "on"}, False, True, False),  # RunOptions wins over the env
        ("on", {"MODEL_LANES": "off"}, True, True, True),
        (None, {"MODEL_LANES": "bogus"}, True, True, False),  # invalid -> auto
    ],
)
def test_switch_resolves_mode_sample_and_configuration(requested, env, sample, configured, enabled):
    config = CFG if configured else NightshiftConfig()
    switch = model_lanes.resolve(requested, sample=sample, config=config, max_calls=12, env=env)
    assert switch.enabled is enabled, switch


def test_cap_zero_switches_the_lanes_off():
    switch = model_lanes.resolve("on", sample=False, config=CFG, max_calls=0, env={})
    assert not switch.enabled and "MAX_MODEL_CALLS_PER_RUN=0" in switch.reason


def test_max_model_calls_is_read_from_env_and_clamped():
    assert LoopBudgets.from_env({}).max_model_calls == 12
    assert LoopBudgets.from_env({"MAX_MODEL_CALLS_PER_RUN": "3"}).max_model_calls == 3
    assert LoopBudgets.from_env({"MAX_MODEL_CALLS_PER_RUN": "999"}).max_model_calls == 50
    assert LoopBudgets.from_env({"MAX_MODEL_CALLS_PER_RUN": "-4"}).max_model_calls == 0


# --- sample mode default ------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_sample_mode_default_makes_zero_model_calls_and_keeps_end_states(
    prefix, rule_only, tmp_path, monkeypatch
):
    # Even with Nightshift configured in the environment, auto stays off in sample mode.
    monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("MODEL_LANES", "auto")
    result = run_investigation(_sample_request(prefix), logs_dir=tmp_path)
    assert result.stats.model_calls == 0 and result.stats.tokens == 0
    assert result.end_state == "actionable"
    assert _shape(result) == _shape(rule_only[prefix])
    assert all(lk.proposed_by == "rules" and lk.model_opinion is None for lk in result.why_tree)
    assert result.action_source == "template" and result.action_template is None
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    mode = next(x for x in lines if x["agent"] == "model_lanes" and x["action"] == "mode")
    assert mode["lanes"] == "off" and "sample mode" in mode["output_summary"]
    assert not [x for x in lines if x.get("lane")]


# --- hypothesiser lane --------------------------------------------------------------------------


def test_proposals_are_validated_and_invalid_ones_rejected():
    frame, root = _frame_03()
    tracer = RecordingTracer()
    rules = rule_propose(frame, root, {root.variable}, max_branches=2, tracer=tracer)
    fake = FakeNightshift(
        propose={
            "candidates": [
                {"type": "concept", "name": "MadeUpConcept", "category": "impairment", "sign": -1},
                {"type": "concept", "name": rules[0].variable, "category": "accounting", "sign": 1},
                {"type": "filing_event", "name": "error_correction"},  # restatements only
                {"type": "guess", "name": "anything"},
                "not an object",
                {"type": "concept", "name": "GoodwillImpairmentLoss", "sign": 1, "why": "x"},
                {"type": "filing_event", "name": "disposal"},  # valid, but no slot left
            ]
        }
    )
    lanes = lanes_for(tracer, fake)
    out = model_lanes.propose(
        lanes,
        frame,
        root,
        rule_candidates=rules,
        path_variables={root.variable},
        exclude=set(),
        free=1,
        tracer=tracer,
    )
    assert [c.variable for c in out] == ["GoodwillImpairmentLoss"]
    [cand] = out
    assert cand.proposed_by == "model" and cand.rule == "event_concept"
    assert cand.node_id == f"n0.{len(rules) + 1}"
    assert cand.params["sign"] == -1  # knowledge wins over the model's sign
    assert cand.category == "impairment"
    rejected = [e for e in tracer.events if e["action"] == "model_reject"]
    assert len(rejected) == 6
    reasons = " | ".join(e["output_summary"] for e in rejected)
    assert "not in the vocabulary" in reasons and "no cycles" in reasons
    assert "does not apply" in reasons and "not an object" in reasons
    assert "free slot" in reasons
    assert lanes.calls == 1


def test_no_free_slot_means_no_model_call():
    frame, root = _frame_03()
    tracer = RecordingTracer()
    fake = FakeNightshift(propose={"candidates": []})
    lanes = lanes_for(tracer, fake)
    out = model_lanes.propose(
        lanes,
        frame,
        root,
        rule_candidates=[],
        path_variables=set(),
        exclude=set(),
        free=0,
        tracer=tracer,
    )
    assert out == [] and fake.calls == []


def test_a_model_candidate_goes_through_analyst_and_critic(tmp_path):
    tracer = Tracer(run_id="lanes-propose", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": [{"type": "concept", "name": "GoodwillImpairmentLoss"}]},
        review={"decision": "accept", "label": "causal", "rationale": "ok"},
        wording=None,
    )
    result = run_loop_a(
        _sample_request("03"),
        tracer=tracer,
        budgets=LoopBudgets(max_branches_per_level=4),
        lanes=lanes_for(tracer, fake),
    )
    model_links = [lk for lk in result.why_tree if lk.proposed_by == "model"]
    assert model_links, "the model's candidate is in the why-tree"
    link = model_links[0]
    assert link.verdict in {"accepted", "pruned", "frontier"} and link.reason
    lines = [json.loads(x) for x in Path(tracer.trace_file).read_text().splitlines()]
    node_lines = [x for x in lines if x.get("node_id") == link.node_id]
    assert any(x["agent"] == "analyst" and x["action"] == "measure" for x in node_lines)
    assert any(x["agent"] == "critic" and x["action"] in {"accept", "prune"} for x in node_lines)
    assert link.evidence and link.evidence[0].sql  # measured through the EvidenceSource
    assert result.stats.model_calls >= 2 and result.stats.tokens == 15 * result.stats.model_calls


# --- critic lane --------------------------------------------------------------------------------


def test_disagreement_goes_to_the_gate_and_auto_keeps_the_rule_verdict(rule_only, tmp_path):
    tracer = Tracer(run_id="lanes-dissent", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": []},
        review={"decision": "prune", "label": "background", "rationale": "Not a cause."},
    )
    result = run_investigation(_sample_request("03"), tracer=tracer, lanes=lanes_for(tracer, fake))
    assert _shape(result) == _shape(rule_only["03"])  # the rules decided
    accepted = [lk for lk in result.why_tree if lk.verdict == "accepted" and lk.model_opinion]
    assert accepted
    for lk in accepted:
        op = lk.model_opinion
        assert not op.agrees and op.gate_choice == "keep-rule" and op.rule_decision == "accept"
        assert op.routed_model == ROUTED and op.receipt_id and op.lane == "deep"
        assert op.rationale == "Not a cause." and op.grounded
    lines = [json.loads(x) for x in Path(tracer.trace_file).read_text().splitlines()]
    gates = [x for x in lines if x["agent"] == "gate" and x.get("proposal") == "keep-rule"]
    assert {g["node_id"] for g in gates} >= {lk.node_id for lk in accepted}
    assert all(
        g["gate"] == "critic_escalation" and g["decided_by"] == "policy:critic_escalation"
        for g in gates
    )
    pruned = [lk for lk in result.why_tree if lk.verdict == "pruned" and lk.model_opinion]
    assert pruned and all(lk.model_opinion.agrees for lk in pruned)


def test_a_scripted_gate_may_prune_on_the_models_dissent(tmp_path):
    tracer = Tracer(run_id="lanes-dissent-scripted", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": []}, review={"decision": "prune", "label": "background"}
    )
    req = _sample_request("03", hitl_mode="scripted", decisions={"critic_escalation:n0.1": "prune"})
    result = run_loop_a(req, tracer=tracer, lanes=lanes_for(tracer, fake))
    node = next(lk for lk in result.why_tree if lk.node_id == "n0.1")
    assert node.verdict == "pruned" and node.model_opinion.gate_choice == "prune"


def test_rationale_with_a_number_not_in_the_measurement_is_dropped(tmp_path):
    tracer = Tracer(run_id="lanes-ungrounded", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": []},
        review={"decision": "accept", "label": "breakdown", "rationale": "It explains 123.4 bn."},
    )
    result = run_loop_a(_sample_request("03"), tracer=tracer, lanes=lanes_for(tracer, fake))
    ops = [lk.model_opinion for lk in result.why_tree if lk.model_opinion]
    assert ops and all(op.rationale is None and not op.grounded for op in ops)


def test_invalid_second_opinion_is_rejected_and_the_link_has_none(tmp_path):
    tracer = RecordingTracer()
    fake = FakeNightshift(propose={"candidates": []}, review={"decision": "maybe", "label": "?"})
    result = run_loop_a(_sample_request("03"), tracer=tracer, lanes=lanes_for(tracer, fake))
    assert all(lk.model_opinion is None for lk in result.why_tree)
    assert any(e["action"] == "model_reject" and e["agent"] == "critic" for e in tracer.events)


# --- reporter lane ------------------------------------------------------------------------------

TEMPLATE = "Re-base the FY2020 Revenues in the model on continuing operations ($10.0B, -$2.0B)."


@pytest.mark.parametrize(
    ("draft", "used"),
    [
        ("Re-base FY2020 Revenues on continuing operations: $10.0B, down -$2.0B.", True),
        ("Re-base FY2020 Revenues on continuing operations: $11.5B.", False),  # new number
        ("Re-base FY2020 Revenues as Acme Corp reported.", False),  # new name
        ("Rebase FY2020 Revenues per the 8-K, on continuing operations at $10.0B.", True),
        ("", False),
    ],
)
def test_reporter_wording_is_used_only_when_grounded_in_the_template(draft, used):
    tracer = RecordingTracer()
    lanes = lanes_for(tracer, FakeNightshift(wording={"action": draft}))
    out = model_lanes.draft_action(lanes, TEMPLATE, "", tracer)
    assert (out is not None) is used
    if not used:
        assert any(e["action"] == "model_reject" for e in tracer.events)


def test_template_stays_in_the_result_when_the_wording_is_used(tmp_path, rule_only):
    tracer = Tracer(run_id="lanes-wording", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": []},
        review=None,  # {} -> invalid, ignored
        wording=lambda user: {"action": user.split("Action: ", 1)[1].split("\nContext:", 1)[0]},
    )
    result = run_investigation(_sample_request("03"), tracer=tracer, lanes=lanes_for(tracer, fake))
    assert result.action_source == "model"
    assert result.action_template == rule_only["03"].action
    assert result.action == rule_only["03"].action  # the echo is grounded


# --- fallback, cap, budget, tracing -------------------------------------------------------------


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_unavailable_or_timeout_degrades_to_the_rule_only_result(exc, rule_only, tmp_path):
    tracer = Tracer(run_id="lanes-down", logs_dir=tmp_path)

    def handler(req):
        raise exc

    result = run_investigation(
        _sample_request("01"), tracer=tracer, lanes=lanes_for(tracer, handler)
    )
    assert _shape(result) == _shape(rule_only["01"])
    assert result.action == rule_only["01"].action and result.status == rule_only["01"].status
    assert result.stats.model_calls == 1  # first failure switches the lanes off
    lines = [json.loads(x) for x in Path(tracer.trace_file).read_text().splitlines()]
    degraded = [x for x in lines if x["action"] == "degrade"]
    assert len(degraded) == 1 and degraded[0]["status"] == "degraded"


def test_cap_is_respected_and_traced(tmp_path):
    tracer = Tracer(run_id="lanes-cap", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": []}, review={"decision": "accept", "label": "breakdown"}
    )
    result = run_investigation(
        _sample_request("03"), tracer=tracer, lanes=lanes_for(tracer, fake, max_calls=2)
    )
    assert result.stats.model_calls == 2 and len(fake.calls) == 2
    lines = [json.loads(x) for x in Path(tracer.trace_file).read_text().splitlines()]
    assert [x for x in lines if x["agent"] == "model_lanes" and x["action"] == "cap"]


def test_no_model_call_when_the_run_budget_is_nearly_spent():
    tracer = RecordingTracer()
    fake = FakeNightshift(propose={"candidates": []})
    lanes = lanes_for(tracer, fake, deadline=10.0, clock=lambda: 8.0)
    assert lanes.ask(lane="fast", agent="hypothesiser", action="x", system="s", user="u") is None
    assert fake.calls == [] and lanes.calls == 0
    assert any(e["action"] == "budget" and e["status"] == "degraded" for e in tracer.events)


def test_every_model_call_writes_one_line_with_routing_and_no_secret(tmp_path):
    tracer = Tracer(run_id="lanes-trace", logs_dir=tmp_path)
    fake = FakeNightshift(
        propose={"candidates": []}, review={"decision": "accept", "label": "breakdown"}
    )
    result = run_investigation(_sample_request("03"), tracer=tracer, lanes=lanes_for(tracer, fake))
    text = Path(tracer.trace_file).read_text()
    assert TOKEN not in text
    lines = [json.loads(x) for x in text.splitlines()]
    calls = [x for x in lines if x.get("requested_model") and x["action"].startswith("model_")]
    assert len(calls) == result.stats.model_calls == len(fake.calls)
    for x in calls:
        assert x["agent"] in {"hypothesiser", "critic", "reporter"}
        assert x["lane"] in {"fast", "deep"} and x["requested_model"].startswith("meterless/")
        assert x["routed_model"] == ROUTED and x["provider"] == "vendor"
        assert x["receipt_id"].startswith("rcpt_") and x["latency_ms"] >= 0
        assert x["status"] == "success" and x["output_summary"]
    assert {x["lane"] for x in calls} == {"fast", "deep"}
    phases = [x for x in lines if x.get("phase") == "model_call"]
    assert len([x for x in phases if x["action"] == "phase_done"]) == len(calls)


def test_env_switch_on_with_an_unreachable_runtime_degrades(rule_only, tmp_path, monkeypatch):
    # The real resolver path (_lanes): MODEL_LANES=on, a configured but unreachable Runtime.
    monkeypatch.setenv("MODEL_LANES", "on")
    monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", TOKEN)
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "2")
    result = run_investigation(_sample_request("02"), logs_dir=tmp_path)
    assert _shape(result) == _shape(rule_only["02"])
    assert result.stats.model_calls == 1
    text = Path(result.trace_file).read_text()
    assert TOKEN not in text and '"action": "degrade"' in text
