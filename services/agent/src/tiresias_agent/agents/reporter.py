"""Reporter: builds the RunResult, leading with the end state and the recommended action
(ADR-0009), then the why-tree (labelled links with their SQL and rows) and the residual."""

from __future__ import annotations

from typing import Any

from ..models import Effect, ErrorDetail, InformationNeed, Link, RunResult, RunStats
from .common import Frame, Node
from .framer import summary as frame_summary
from .knowledge import ACTIONS, EXTERNAL_ACTION, RECONCILED_ACTIONS, actionable_family, money, pct

AGENT = "reporter"
SUCCESS_STATES = {"actionable", "branched", "cyclic", "external-driver"}


def effect_of(frame: Frame) -> Effect:
    return Effect(
        entity=frame.ticker.upper(),
        metric=frame.metric,
        kind=frame.kind,
        period={"start": frame.period_start, "end": frame.period_end},
        baseline=frame.baseline,
        target=frame.target,
        delta=frame.delta,
        relative_change=frame.relative_change,
        summary=frame_summary(frame),
    )


def link(node: Node) -> Link:
    return Link(
        node_id=node.node_id,
        parent_id=node.parent_id,
        claim=node.claim,
        label=node.label,
        evidence_status=node.evidence_status,
        share_explained=node.share,
        residual=_residual(node),
        sql=node.evidence[-1].sql if node.evidence else None,
        depth=node.depth,
        verdict=node.verdict,
        rule=node.rule,
        reason=node.reason,
        attempts=node.attempts,
        evidence=node.evidence,
    )


def _residual(node: Node) -> float | None:
    """Loop B links carry a signed residual (negative = over-explained); others 1 - share."""
    if node.context.get("residual") is not None:
        return node.context["residual"]
    return None if node.share is None else round(1 - node.share, 4)


def driver(node: Node) -> str:
    m = node.measurement
    if m and m.variable_target is not None and m.variable_baseline is not None:
        return (
            f"{node.variable} of {money(m.variable_target)} (baseline {money(m.variable_baseline)})"
        )
    return node.variable


def event_text(node: Node) -> str:
    m = node.measurement
    if m and m.events:
        e = m.events[-1]
        return f"{e.form} items {e.items}, filed {e.filing_date}"
    return node.variable


def action_for(frame: Frame, node: Node, family: str) -> str | None:
    actions = RECONCILED_ACTIONS if node.rule == "reconcile" else ACTIONS
    template = actions.get((node.category or "", family))
    if template is None:
        return None
    return template.format(
        **node.context.get("format", {}),
        period=frame.period_label,
        metric=frame.metric,
        target=money(frame.target.value),
        baseline=money(frame.baseline.value),
        delta=money(frame.delta),
        event=event_text(node),
        driver=driver(node),
        share=pct(node.share),
    )


def build(
    *,
    run_id: str,
    end_state: str | None,
    frame: Frame | None,
    nodes: list[Node],
    chosen: list[Node],
    need: InformationNeed | None,
    stats: RunStats,
    trace_file: str | None,
    sample_mode: bool,
    parent_run_id: str | None,
    tracer: Any,
    error: ErrorDetail | None = None,
    note: str | None = None,
) -> RunResult:
    action = root_cause = None
    residual: float | None = None
    asker = frame.asker if frame else None
    levers = list(asker.levers) if asker else []
    role = asker.role if asker else None
    explained = sum(n.share or 0 for n in chosen if n.verdict == "accepted")
    if frame is not None and end_state is not None:
        residual = round(max(0.0, 1.0 - explained), 4)
    if frame is not None and end_state == "actionable" and chosen:
        main = chosen[0]
        fam = actionable_family(main.category or "", levers, role)
        action = action_for(frame, main, fam) if fam else None
        root_cause = (
            f"{main.claim}; {main.reason}"
            if main.rule == "reconcile"
            else f"{main.claim}: {driver(main)}; {main.reason}"
        )
    elif frame is not None and end_state == "branched":
        parts = [f"{n.claim} ({pct(n.share)})" for n in chosen]
        root_cause = "Several causes each explain a share: " + "; ".join(parts)
        acts = []
        for n in chosen:
            fam = actionable_family(n.category or "", levers, role)
            if fam:
                acts.append(action_for(frame, n, fam))
        action = " ".join(a for a in acts if a) or "Monitor each branch; none is in your levers."
    elif frame is not None and end_state == "external-driver" and chosen:
        main = chosen[0]
        root_cause = (
            f"{main.claim}; {main.reason}"
            if main.rule == "reconcile"
            else f"{main.claim}: {driver(main)}"
        )
        action = EXTERNAL_ACTION.format(driver=driver(main), levers=", ".join(levers) or role)
    elif end_state == "evidence-frontier":
        main = chosen[0] if chosen else None
        if frame is not None and main is not None:
            root_cause = f"Hypothesis ({main.evidence_status}): {main.claim}. {main.reason}"
            fam = actionable_family(main.category or "", levers, role)
            interim = action_for(frame, main, fam) if fam else None
            action = (interim + " " if interim else "") + (
                f"Next: onboard a source for {need.variable} of the {need.entity_hint.relation} "
                f"({need.period.start}..{need.period.end}) and run: {need.test}"
                if need
                else ""
            )
        elif need is not None:
            root_cause = f"Not measurable from the registered sources: {need.reason}"
            action = (
                f"Onboard a source for {need.variable} ({need.entity_hint.description}); "
                "then re-run."
            )
    elif end_state == "budget":
        best = max(
            (n for n in nodes if n.verdict == "accepted"), key=lambda n: n.share or 0, default=None
        )
        root_cause = (
            f"Incomplete (budget reached): best link so far is {best.claim}" if best else None
        )
        action = "Incomplete: re-run with a larger budget, or branch from the best node."
        if note:
            action += f" ({note})"
    if (
        chosen
        and end_state in {"actionable", "external-driver"}
        and chosen[0].context.get("residual") is not None
    ):
        residual = chosen[0].context["residual"]  # signed (Loop B reconciliation)
    if residual is not None and end_state in {"evidence-frontier", "budget"}:
        residual = round(
            max(
                0.0,
                1.0
                - sum(
                    n.share or 0 for n in nodes if n.verdict == "accepted" and n.label == "causal"
                ),
            ),
            4,
        )

    status = "error" if error else ("success" if end_state in SUCCESS_STATES else "partial")
    result = RunResult(
        end_state=end_state,
        action=action,
        root_cause=root_cause,
        residual=residual,
        run_id=run_id,
        parent_run_id=parent_run_id,
        status=status,
        effect=effect_of(frame) if frame else None,
        information_need=need,
        why_tree=[link(n) for n in nodes],
        stats=stats,
        trace_file=trace_file,
        sample_mode=sample_mode,
        error=error,
    )
    tracer.emit(
        agent=AGENT,
        action="report",
        status="error" if error else "success",
        node_id=chosen[0].node_id if chosen else None,
        output_summary=f"{end_state}: {action}" if end_state else (error.message if error else ""),
        end_state=end_state,
        residual=residual,
    )
    return result
