"""Hypothesiser: generic rules, capped per level, no cycles."""

from loop_helpers import RecordingTracer, request

from tiresias_agent.adapters.zetaris import SampleSource
from tiresias_agent.agents.common import Node
from tiresias_agent.agents.framer import frame
from tiresias_agent.agents.hypothesiser import propose
from tiresias_agent.agents.knowledge import CONTINUING_OPS, DISCONTINUED_OPS


def _root(f):
    return Node("n0", None, 0, f.metric, "root", "breakdown", "root", effect=f.delta)


def test_restated_revenue_proposes_filing_events_and_concept_switch():
    f = frame(request("01"), SampleSource(), RecordingTracer())
    tracer = RecordingTracer()
    cands = propose(f, _root(f), {f.metric}, max_branches=3, tracer=tracer)
    assert [c.rule for c in cands] == ["filing_event", "filing_event", "concept_switch"]
    assert cands[0].params == {"event": "disposal"} and cands[0].category == "portfolio_change"
    assert [c.node_id for c in cands] == ["n0.1", "n0.2", "n0.3"]
    proposals = [e for e in tracer.events if e["action"] == "propose"]
    assert len(proposals) == 3 and all(e["target_agent"] == "analyst" for e in proposals)


def test_net_income_change_proposes_hierarchy_children_then_comparative():
    f = frame(request("03"), SampleSource(), RecordingTracer())
    cands = propose(f, _root(f), {f.metric}, max_branches=3, tracer=RecordingTracer())
    assert [c.variable for c in cands[:2]] == [CONTINUING_OPS, DISCONTINUED_OPS]
    assert cands[2].rule == "comparative_restated"


def test_event_concepts_are_proposed_under_the_component_they_roll_into():
    f = frame(request("03"), SampleSource(), RecordingTracer())
    parent = Node("n0.1", "n0", 1, CONTINUING_OPS, "c", "breakdown", "accepted")
    cands = propose(f, parent, {f.metric, CONTINUING_OPS}, max_branches=3, tracer=RecordingTracer())
    assert [(c.rule, c.category, c.depth) for c in cands] == [("event_concept", "impairment", 2)]


def test_cap_and_path_exclusion():
    f = frame(request("03"), SampleSource(), RecordingTracer())
    tracer = RecordingTracer()
    cands = propose(f, _root(f), {f.metric, CONTINUING_OPS}, max_branches=1, tracer=tracer)
    assert len(cands) == 1 and cands[0].variable == DISCONTINUED_OPS
    (cap,) = [e for e in tracer.events if e["action"] == "cap"]
    assert CONTINUING_OPS in cap["output_summary"]


def test_already_tested_variables_are_not_proposed_again():
    f = frame(request("01"), SampleSource(), RecordingTracer())
    first = propose(f, _root(f), {f.metric}, max_branches=3, tracer=RecordingTracer())
    again = propose(
        f,
        _root(f),
        {f.metric},
        max_branches=3,
        tracer=RecordingTracer(),
        exclude={c.variable for c in first},
    )
    assert again == []
