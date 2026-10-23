"""Types the Loop A agents pass to each other, and small helpers over evidence rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from ..adapters.zetaris import Evidence, Fact, Filing
from ..models import Asker, EffectKind, EvidenceItem, EvidenceStatus, LinkLabel, Observation

ROWS_SHOWN = 10

# Critic thresholds (causal-criteria.md §1): "most of the effect", a share worth keeping, the
# band around "most" in which the critic escalates, and over-explanation.
MOST_OF_EFFECT = 0.5
MIN_SHARE = 0.2
ESCALATION_MARGIN = 0.05
OVER_EXPLAINED = 1.05
LEVERAGE_MAX_RELATIVE_CHANGE = 0.10


class LoopError(Exception):
    """A controlled failure: the run ends with a structured error, not a stack trace."""

    def __init__(self, error_type: str, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.recoverable = recoverable


@dataclass
class Frame:
    """The framed question: one metric of one entity, target vs baseline, like-for-like."""

    question: str
    ticker: str
    cik: str | None
    metric: str
    aliases: tuple[str, ...]
    period_start: str
    period_end: str
    kind: EffectKind
    baseline: Observation
    target: Observation
    asker: Asker
    evidence: EvidenceItem

    @property
    def delta(self) -> float:
        return self.target.value - self.baseline.value

    @property
    def relative_change(self) -> float | None:
        return None if self.baseline.value == 0 else self.delta / abs(self.baseline.value)

    @property
    def period_label(self) -> str:
        start, end = date.fromisoformat(self.period_start), date.fromisoformat(self.period_end)
        return f"FY{end.year}" if 350 <= (end - start).days <= 380 else f"{start}..{end}"

    def window(self) -> tuple[str, str]:
        """(after, until]: when an event must happen to cause this effect (§2.2, earlier in
        time). A restatement of a closed period is caused after the period ends and no later
        than the restating filing; a change is caused during the period."""
        if self.kind == "restatement":
            return self.period_end, self.target.filed or self.period_end
        return _day_before(self.period_start), self.period_end


def _day_before(iso: str) -> str:
    return date.fromordinal(date.fromisoformat(iso).toordinal() - 1).isoformat()


@dataclass
class Node:
    """A why-tree node as the orchestrator tracks it."""

    node_id: str
    parent_id: str | None
    depth: int
    variable: str
    claim: str
    label: LinkLabel
    verdict: str
    rule: str | None = None
    category: str | None = None
    evidence_status: EvidenceStatus | None = None
    share: float | None = None
    raw_share: float | None = None
    effect: float | None = None
    reason: str | None = None
    attempts: int = 1
    evidence: list[EvidenceItem] = field(default_factory=list)
    measurement: Measurement | None = None


@dataclass
class Candidate:
    """A hypothesis the hypothesiser proposes for the analyst to test."""

    node_id: str
    parent_id: str
    depth: int
    rule: str  # component | event_concept | filing_event | concept_switch | comparative_restated
    variable: str
    claim: str
    category: str
    params: dict[str, Any] = field(default_factory=dict)


Scope = Literal["broad", "window", "relaxed"]


@dataclass
class Measurement:
    """What the analyst found for one candidate on one attempt."""

    candidate_id: str
    attempt: int
    scope: Scope
    evidence: list[EvidenceItem] = field(default_factory=list)
    effect: float | None = None  # contribution to the root delta, in the metric's units
    variable_baseline: float | None = None
    variable_target: float | None = None
    counterfactual: float | None = None  # metric value with the candidate reset to baseline
    events: list[Filing] = field(default_factory=list)
    events_outside_window: int = 0
    mechanism: list[str] = field(default_factory=list)
    testable: bool = True
    missing: str | None = None
    error: str | None = None

    @property
    def primary(self) -> EvidenceItem | None:
        """The query that tests the candidate itself (later ones add mechanism or magnitude)."""
        return self.evidence[0] if self.evidence else None

    @property
    def rows(self) -> int:
        return self.primary.matched if self.primary else 0

    @property
    def primary_sql(self) -> str | None:
        return self.primary.sql if self.primary else None


Decision = Literal["accept", "prune", "retry", "frontier"]


@dataclass
class Verdict:
    decision: Decision
    label: LinkLabel
    reason: str
    evidence_status: EvidenceStatus | None = None
    share: float | None = None
    raw_share: float | None = None
    retry_scope: Scope | None = None
    escalation: dict[str, Any] | None = None  # gate request the critic wants decided


# --- Evidence helpers ---------------------------------------------------------------------------


def fact_row(f: Fact) -> dict[str, Any]:
    return {
        "concept": f.concept,
        "val": f.val,
        "start": f.start,
        "end": f.end,
        "form": f.form,
        "filed": f.filed,
        "accn": f.accn,
    }


def filing_row(f: Filing) -> dict[str, Any]:
    return {
        "accession_number": f.accession_number,
        "filing_date": f.filing_date,
        "report_date": f.report_date,
        "form": f.form,
        "items": f.items,
    }


def evidence_item(
    result: Evidence[Any], rows: list[dict[str, Any]], *, filter: str | None = None
) -> EvidenceItem:
    return EvidenceItem(
        sql=result.sql,
        filter=filter,
        source=result.source,
        row_count=len(result),
        matched=len(rows),
        rows=rows[:ROWS_SHOWN],
        degraded=bool(result.degraded),
        note=result.note,
    )


def same_period(f: Fact, start: str | None, end: str) -> bool:
    return f.end == end and (f.start == start or start is None or f.start is None)


def observation(f: Fact) -> Observation:
    return Observation(
        value=float(f.val),
        concept=f.concept,
        start=f.start,
        end=f.end,
        form=f.form,
        filed=f.filed,
        accn=f.accn,
    )


def value_in(facts: list[Fact], obs: Observation) -> Fact | None:
    """The fact for obs's period reported in obs's own filing (like-for-like)."""
    for f in facts:
        if f.accn == obs.accn and same_period(f, obs.start, obs.end):
            return f
    return None


def latest_for_period(facts: list[Fact], start: str | None, end: str) -> Fact | None:
    matching = [f for f in facts if same_period(f, start, end)]
    return max(matching, key=lambda f: f.filed or "") if matching else None


def shift_year(iso: str | None, years: int) -> str | None:
    if iso is None:
        return None
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + years, day=28).isoformat()
