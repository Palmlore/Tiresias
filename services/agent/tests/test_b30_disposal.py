"""B-30: a disposal measured on the filer's own disposed-group revenue, cited honestly.

The shape is JNJ FY2022 in the 2026-10-26 coverage sweep (docs/measurements/coverage-sweep-2026-10-26.md):
two 8-K item 2.01 filings in the restatement window (an acquisition first, then the separation)
and a DisposalGroupIncludingDiscontinuedOperationRevenue fact in the restating 10-K. Synthetic
values; no data files.
"""

from tiresias_agent.adapters.zetaris import Evidence, Fact, Filing
from tiresias_agent.agents import analyst, reporter
from tiresias_agent.agents.common import Candidate, Frame, Measurement, Node
from tiresias_agent.agents.critic import judge
from tiresias_agent.models import Asker, EvidenceItem, Observation

DISPOSAL = "DisposalGroupIncludingDiscontinuedOperationRevenue"


class _Tracer:
    def emit(self, **kw):
        return None


def _frame() -> Frame:
    obs = {"concept": "Revenues", "start": "2021-01-04", "end": "2022-01-02", "form": "10-K"}
    return Frame(
        question="why?",
        ticker="xyz",
        cik=None,
        metric="Revenues",
        aliases=("Revenues",),
        period_start="2021-01-04",
        period_end="2022-01-02",
        kind="restatement",
        baseline=Observation(value=93.78e9, filed="2023-02-16", accn="base", **obs),
        target=Observation(value=78.74e9, filed="2024-02-16", accn="tgt", **obs),
        asker=Asker(role="analyst", levers=["model adjustments"]),
        evidence=EvidenceItem(sql="SELECT 1", source="test", row_count=2),
    )


class _Source:
    name = "test"

    def __init__(self, disposal_val: float | None) -> None:
        self.disposal_val = disposal_val

    def filings(self, entity, forms=("8-K",)):
        rows = [  # newest first, as the filings SQL orders them
            Filing("k2", "2023-08-25", None, "8-K", "2.01", "sep.htm"),
            Filing("k1", "2022-12-22", None, "8-K", "1.01,2.01,7.01,9.01", "acq.htm"),
        ]
        return Evidence(rows, sql="SELECT filings", source="test")

    def concept_series(self, entity, concept):
        facts = []
        if concept == DISPOSAL and self.disposal_val is not None:
            facts.append(
                Fact(
                    DISPOSAL,
                    "USD",
                    self.disposal_val,
                    "2021-01-04",
                    "2022-01-02",
                    2023,
                    "FY",
                    "10-K",
                    "2024-02-16",
                    "tgt",
                )
            )
        return Evidence(facts, sql=f"SELECT {concept}", source="test")


def _cand() -> Candidate:
    return Candidate(
        "n0.1",
        "n0",
        1,
        "filing_event",
        "8-K 2.01",
        "claim",
        "disposal",
        params={"event": "disposal"},
    )


def _measure(disposal_val):
    return analyst.measure(
        _cand(), _frame(), _Source(disposal_val), attempt=0, scope="window", tracer=_Tracer()
    )


def test_measured_disposal_names_the_fact_it_rests_on():
    m = _measure(15.035e9)
    assert m.effect == -15.035e9
    assert any(DISPOSAL in s and "10-K filed 2024-02-16" in s for s in m.mechanism), m.mechanism
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert v.decision == "accept"  # measured on the filer's own fact: a real share, not asserted


def test_unmeasured_disposal_is_not_accepted():
    m = _measure(None)
    assert m.effect is None and m.missing
    v = judge(_cand(), m, _frame(), retries_left=1)
    assert v.decision != "accept"


def test_several_matching_filings_are_all_named_not_the_oldest():
    m = _measure(15.035e9)
    node = Node("n0.1", "n0", 1, "8-K 2.01", "claim", "causal", "accept", measurement=m)
    text = reporter.event_text(node)
    assert text.startswith("one of 2 filings in the window: ")
    assert text.index("2022-12-22") < text.index("2023-08-25")  # both, in date order


def test_one_matching_filing_keeps_the_original_wording():
    m = Measurement(candidate_id="n0.1", attempt=0, scope="window")
    m.events = [Filing("k", "2024-04-04", None, "8-K", "2.01,9.01", None)]
    node = Node("n0.1", "n0", 1, "8-K 2.01", "claim", "causal", "accept", measurement=m)
    assert reporter.event_text(node) == "8-K items 2.01,9.01, filed 2024-04-04"
