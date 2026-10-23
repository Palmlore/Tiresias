"""Critic: causal-criteria.md as pure rules over synthetic measurements (no data files)."""

import pytest

from tiresias_agent.adapters.zetaris import Filing
from tiresias_agent.agents.common import Candidate, Frame, Measurement
from tiresias_agent.agents.critic import apply_gate, judge
from tiresias_agent.models import Asker, EvidenceItem, Observation


def _frame(baseline=100.0, target=60.0, kind="change"):
    obs = {"concept": "M", "start": "2001-01-01", "end": "2001-12-31", "form": "10-K"}
    return Frame(
        question="why?",
        ticker="xyz",
        cik=None,
        metric="M",
        aliases=("M",),
        period_start="2001-01-01",
        period_end="2001-12-31",
        kind=kind,
        baseline=Observation(value=baseline, filed="2002-02-01", accn="a", **obs),
        target=Observation(value=target, filed="2003-02-01", accn="b", **obs),
        asker=Asker(role="analyst", levers=["estimates"]),
        evidence=EvidenceItem(sql="SELECT 1", source="test", row_count=2),
    )


def _cand(rule="event_concept", category="impairment"):
    return Candidate("n0.1", "n0", 1, rule, "X", "claim", category)


def _m(scope="window", **kw):
    return Measurement(candidate_id="n0.1", attempt=0, scope=scope, **kw)


def _filing(date):
    return Filing("acc-" + date, date, None, "8-K", "2.01", None)


def test_breakdown_component_accepted_when_it_locates_enough():
    v = judge(_cand("component", "location"), _m(effect=-30.0), _frame(), retries_left=1)
    assert (v.decision, v.label, v.evidence_status) == ("accept", "breakdown", "evidenced")
    assert v.share == 0.75


def test_breakdown_component_pruned_when_small():
    v = judge(_cand("component", "location"), _m(effect=-4.0), _frame(), retries_left=1)
    assert (v.decision, v.label) == ("prune", "breakdown")


def test_causal_accepted_with_intervention_share():
    m = _m(effect=-32.0, variable_baseline=10.0, variable_target=42.0, counterfactual=92.0)
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert (v.decision, v.label, v.evidence_status, v.share) == (
        "accept",
        "causal",
        "evidenced",
        0.8,
    )
    assert v.escalation is None


def test_causal_partial_share_is_partially_evidenced():
    m = _m(effect=-12.0, variable_baseline=10.0, variable_target=22.0)
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert (v.decision, v.evidence_status) == ("accept", "partially-evidenced")


def test_over_explaining_cause_escalates_with_capped_share():
    m = _m(effect=-60.0, variable_baseline=10.0, variable_target=70.0)
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert v.decision == "accept" and v.share == 1.0 and v.raw_share == 1.5
    assert v.escalation["proposal"] == "accept-capped"


def test_borderline_share_escalates():
    m = _m(effect=-20.0, variable_baseline=10.0, variable_target=30.0)
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert v.escalation is not None and set(v.escalation["options"]) >= {"accept", "prune"}


def test_gate_prune_overrides_accept():
    m = _m(effect=-60.0, variable_baseline=10.0, variable_target=70.0)
    v = apply_gate(judge(_cand(), m, _frame(), retries_left=1), "prune")
    assert v.decision == "prune"


def test_no_abnormal_change_is_background():
    m = _m(effect=0.0, variable_baseline=5.0, variable_target=5.0)
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert (v.decision, v.label) == ("prune", "background")


def test_small_relative_change_with_large_share_is_leverage():
    m = _m(effect=-30.0, variable_baseline=1000.0, variable_target=1030.0)
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert (v.decision, v.label) == ("accept", "leverage")


def test_broad_event_query_is_sent_back_to_the_analyst_narrowed():
    m = _m("broad", events=[_filing("1990-01-01"), _filing("2002-06-01")], events_outside_window=1)
    v = judge(
        _cand("filing_event", "portfolio_change"), m, _frame(kind="restatement"), retries_left=1
    )
    assert (v.decision, v.retry_scope) == ("retry", "window")
    assert "temporal test" in v.reason


def test_retry_budget_spent_prunes():
    m = _m("broad", events=[_filing("1990-01-01")], events_outside_window=1)
    v = judge(_cand("filing_event", "portfolio_change"), m, _frame(), retries_left=0)
    assert v.decision == "prune"


def test_untestable_event_goes_to_the_frontier_via_the_gate():
    m = _m(
        events=[_filing("2002-06-01")],
        mechanism=["re-presented"],
        testable=False,
        missing="M of the disposed business",
    )
    v = judge(_cand("filing_event", "portfolio_change"), m, _frame(), retries_left=0)
    assert (v.decision, v.label, v.evidence_status) == ("frontier", "causal", "partially-evidenced")
    assert v.escalation["proposal"] == "frontier"


def test_no_evidence_prunes_keeping_the_intended_label():
    v = judge(_cand("filing_event", "accounting"), _m(), _frame(), retries_left=1)
    assert (v.decision, v.label, v.evidence_status) == ("prune", "causal", "hypothesis")


def test_missing_component_is_retried_relaxed_then_pruned():
    m = _m(missing="X for target period/filing")
    v = judge(_cand("component", "location"), m, _frame(), retries_left=1)
    assert (v.decision, v.retry_scope) == ("retry", "relaxed")
    m2 = _m("relaxed", missing="X for target period/filing")
    assert judge(_cand("component", "location"), m2, _frame(), retries_left=0).decision == "prune"


def test_restated_comparative_is_background():
    m = _m(mechanism=["prior value restated"], effect=3.0)
    v = judge(_cand("comparative_restated", "location"), m, _frame(), retries_left=1)
    assert (v.decision, v.label) == ("prune", "background")


@pytest.mark.parametrize("error", ["source error: down", "query not possible: bad"])
def test_analyst_error_is_pruned_not_raised(error):
    v = judge(_cand(), _m(error=error), _frame(), retries_left=1)
    assert v.decision == "prune" and error in v.reason
