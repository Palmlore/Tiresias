"""Gap analyst: writes the information need when no registered source can test a candidate.

This is the Loop A -> Loop B hand-off (docs/architecture.md). The need names what to fetch
(variable and its aliases), where it probably lives (an entity hint taken from the run's own
evidence, e.g. the 8-K that reported a disposal), for which period, how to join it (the
catalog's join keys), how big it should be, and the intervention test to run once it is
registered. Loop B's source scout picks the catalog template; this module never guesses one.
"""

from __future__ import annotations

from typing import Any

from ..models import EntityHint, InformationNeed, Period
from .common import Frame, Node
from .knowledge import money

AGENT = "gap_analyst"
JOIN_KEY = ["cik", "concept", "period_start", "period_end"]
RECONCILE_TOLERANCE = 0.05


def for_candidate(frame: Frame, node: Node, tracer: Any) -> InformationNeed:
    """A need for a candidate the critic sent to the frontier."""
    m = node.measurement
    event = m.events[-1] if m and m.events else None
    if event is not None:
        hint = EntityHint(
            relation="disposed business" if node.category == "portfolio_change" else "event",
            source_ticker=frame.ticker.upper(),
            source_cik=frame.cik,
            filing_accession=event.accession_number,
            filing_date=event.filing_date,
            filing_items=[i for i in (event.items or "").split(",") if i],
            description=(
                f"the business {frame.ticker.upper()} reported disposing of in its "
                f"{event.form} filed {event.filing_date} (items {event.items}); if it became a "
                "separate registrant, its own filings report the missing amount"
            ),
        )
    else:
        hint = EntityHint(
            relation="same entity, finer detail",
            source_ticker=frame.ticker.upper(),
            source_cik=frame.cik,
            description=f"a finer breakdown of {node.variable} for {frame.ticker.upper()}",
        )
    unexplained = frame.delta
    test = (
        f"Intervention: add the disposed business's {frame.metric} for {frame.period_label} to "
        f"the restated {money(frame.target.value)}; the restatement is explained if the sum is "
        f"within {RECONCILE_TOLERANCE:.0%} of {money(frame.baseline.value)}."
        if frame.kind == "restatement" and node.category == "portfolio_change"
        else f"Intervention: reset {node.variable} to its baseline and re-measure "
        f"{frame.metric}; it passes if most of the {money(unexplained)} effect disappears."
    )
    need = InformationNeed(
        node_id=node.node_id,
        variable=frame.metric,
        variable_aliases=list(frame.aliases[1:]),
        entity_hint=hint,
        period=Period(start=frame.period_start, end=frame.period_end),
        join_key=JOIN_KEY,
        expected_magnitude=abs(unexplained),
        test=test,
        reason=node.reason or "no registered source can test the candidate",
    )
    _trace(tracer, need)
    return need


def for_variable(
    frame: Frame | None,
    *,
    variable: str,
    node_id: str,
    reason: str,
    tracer: Any,
    ticker: str,
    cik: str | None,
    period: Period,
) -> InformationNeed:
    """A need for a variable no registered source holds (or no rule could explain further)."""
    need = InformationNeed(
        node_id=node_id,
        variable=variable,
        variable_aliases=list(frame.aliases[1:]) if frame and variable == frame.metric else [],
        entity_hint=EntityHint(
            relation="same entity",
            source_ticker=ticker.upper(),
            source_cik=cik,
            description=f"{variable} (or its drivers) for {ticker.upper()}, not in the "
            "registered sources",
        ),
        period=period,
        join_key=JOIN_KEY,
        expected_magnitude=abs(frame.delta) if frame else None,
        test=f"Register a source with {variable} for the period, then re-run Loop A on it.",
        reason=reason,
    )
    _trace(tracer, need)
    return need


def _trace(tracer: Any, need: InformationNeed) -> None:
    tracer.emit(
        agent=AGENT,
        action="information_need",
        node_id=need.node_id,
        target_agent="source_scout",
        output_summary=f"need {need.variable} for {need.entity_hint.relation} "
        f"({need.period.start}..{need.period.end}), join on {', '.join(need.join_key)}",
        information_need=need.model_dump(mode="json"),
    )
