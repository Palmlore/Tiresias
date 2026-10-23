"""Reconcile (Loop B): the analyst queries the newly registered entity, the critic runs the
intervention test the information need names (docs/causal-criteria.md §1).

Add-back test for a restatement caused by a disposal: put the disposed business's own value of
the metric for the period back into the restated figure (``restated + disposed``) and compare it
with the original figure. The share explained is ``disposed / (original - restated)``; the
residual is what is left (signed: negative when the add-back over-explains, i.e. offsetting
items exist, such as sales between the two businesses). Nothing downstream is frozen.

Traced as agent ``analyst`` (measure) and ``critic`` (verdict) with ``loop: "B"``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..adapters.zetaris import Entity, EvidenceSource, Fact, SourceError
from ..models import EvidenceItem, InformationNeed
from .common import (
    MIN_SHARE,
    MOST_OF_EFFECT,
    OVER_EXPLAINED,
    Frame,
    Verdict,
    evidence_item,
    fact_row,
    same_period,
)
from .gap_analyst import RECONCILE_TOLERANCE
from .knowledge import ANNUAL_FORMS, money, pct, residual_text

ANALYST = "analyst"
CRITIC = "critic"


@dataclass
class Reconciliation:
    """What the Loop B analyst found for the registered entity."""

    entity: Entity
    evidence: list[EvidenceItem] = field(default_factory=list)
    fact: Fact | None = None
    value: float | None = None  # the disposed business's own metric for the period
    counterfactual: float | None = None  # restated + disposed
    effect: float | None = None  # contribution to the root delta (the disposal removed it)
    residual_amount: float | None = None  # delta - effect = counterfactual - original
    others: list[float] = field(default_factory=list)  # other reported values for the period
    missing: str | None = None
    error: str | None = None

    @property
    def sql(self) -> str | None:
        return self.evidence[0].sql if self.evidence else None

    @property
    def rows(self) -> int:
        return self.evidence[0].matched if self.evidence else 0


def measure(
    frame: Frame,
    need: InformationNeed,
    entity: Entity,
    source: EvidenceSource,
    *,
    node_id: str,
    tracer: Any,
) -> Reconciliation:
    start_t = time.perf_counter()
    r = Reconciliation(entity=entity)
    start, end = need.period.start.isoformat(), need.period.end.isoformat()
    try:
        series = source.concept_series(entity.ticker, need.variable)
        facts = [f for f in series if same_period(f, start, end)]
        r.evidence.append(
            evidence_item(
                series,
                [fact_row(f) for f in sorted(facts, key=lambda f: f.filed or "")],
                filter=f"period_start = '{start}' AND period_end = '{end}'",
            )
        )
        annual = [f for f in facts if (f.form or "") in ANNUAL_FORMS] or facts
        if not annual:
            r.missing = f"{need.variable} of {entity.name} for {start}..{end}"
        else:
            # Its first annual report of the period: the figure as it stood when it separated.
            r.fact = min(annual, key=lambda f: f.filed or "")
            r.value = float(r.fact.val)
            r.others = sorted({float(f.val) for f in annual} - {r.value})
            if frame.kind != "restatement":
                r.missing = "an add-back test for a change between periods (restatements only)"
            else:
                r.effect = -r.value
                r.counterfactual = frame.target.value + r.value
                r.residual_amount = frame.delta - r.effect
    except SourceError as err:
        r.error = f"source error: {err}"
    except ValueError as err:
        r.error = f"query not possible: {err}"
    tracer.emit(
        agent=ANALYST,
        action="measure",
        status="error" if r.error else "success",
        node_id=node_id,
        parent_node_id=need.node_id,
        target_agent=CRITIC,
        loop="B",
        input_summary=f"reconcile: {need.variable} of {entity.name} (CIK {entity.cik}), "
        f"{start}..{end}",
        output_summary=_summary(r, frame),
        sql=r.sql,
        filter=r.evidence[0].filter if r.evidence else None,
        rows=r.rows,
        entity={"ticker": entity.ticker, "cik": entity.cik, "name": entity.name},
        duration_ms=round((time.perf_counter() - start_t) * 1000),
    )
    return r


def _summary(r: Reconciliation, frame: Frame) -> str:
    if r.error:
        return r.error
    if r.value is None or r.counterfactual is None:
        return f"missing: {r.missing}" if r.missing else "no evidence"
    return (
        f"{r.entity.name} reports {money(r.value)} ({r.fact.form if r.fact else '?'} filed "
        f"{r.fact.filed if r.fact else '?'}); restated {money(frame.target.value)} + "
        f"{money(r.value)} = {money(r.counterfactual)} vs original {money(frame.baseline.value)}"
    )


def judge(frame: Frame, r: Reconciliation) -> tuple[Verdict, float | None]:
    """The critic's verdict on the add-back, and the signed residual share. Pure."""
    if r.error:
        return Verdict("prune", "causal", f"not testable: {r.error}", "hypothesis"), None
    if r.effect is None or r.counterfactual is None or frame.delta == 0:
        return (
            Verdict(
                "frontier",
                "causal",
                f"the registered source does not show {r.missing or 'the amount'}",
                "partially-evidenced",
            ),
            None,
        )
    raw = r.effect / frame.delta
    share = round(max(0.0, min(1.0, raw)), 4)
    residual = round(1.0 - raw, 4)
    gap = abs(r.counterfactual - frame.baseline.value)
    tolerance = RECONCILE_TOLERANCE * abs(frame.baseline.value)
    test = (
        f"add-back: restated {money(frame.target.value)} + {r.entity.name} {money(r.value)} = "
        f"{money(r.counterfactual)} vs original {money(frame.baseline.value)} "
        f"(gap {money(r.counterfactual - frame.baseline.value)}, tolerance "
        f"{RECONCILE_TOLERANCE:.0%} = {money(tolerance)}); explains {pct(raw)} of the "
        f"{money(frame.delta)} restatement; residual {residual_text(r.residual_amount, residual)}"
    )
    if raw < MIN_SHARE:
        return (
            Verdict("prune", "causal", test + f"; below {pct(MIN_SHARE)}", "evidenced", share, raw),
            residual,
        )
    if raw > OVER_EXPLAINED and gap > tolerance:
        return (
            Verdict(
                "accept",
                "causal",
                test + "; it over-explains beyond tolerance, so offsetting items exist",
                "partially-evidenced",
                share,
                raw,
                escalation={
                    "question": f"The add-back explains {pct(raw)} of the restatement (offsets of "
                    f"{money(-(r.residual_amount or 0))}). Accept with share capped at 100%, "
                    "or prune?",
                    "options": ("accept-capped", "prune"),
                    "proposal": "accept-capped",
                },
            ),
            residual,
        )
    if raw >= MOST_OF_EFFECT and gap <= tolerance:
        return (
            Verdict("accept", "causal", test + "; reconciled", "evidenced", share, raw),
            residual,
        )
    return (
        Verdict(
            "accept",
            "causal",
            test + "; explains a share, not reconciled within tolerance",
            "partially-evidenced",
            share,
            raw,
        ),
        residual,
    )


def trace_verdict(
    tracer: Any, node_id: str, r: Reconciliation, v: Verdict, residual: float | None
) -> None:
    tracer.emit(
        agent=CRITIC,
        action=v.decision,
        status="rejected" if v.decision == "prune" else "success",
        node_id=node_id,
        target_agent={"accept": "orchestrator", "prune": "reporter", "frontier": "reporter"}.get(
            v.decision, "orchestrator"
        ),
        loop="B",
        label=v.label,
        evidence_status=v.evidence_status,
        share_explained=v.share,
        raw_share=None if v.raw_share is None else round(v.raw_share, 4),
        residual=residual,
        residual_amount=r.residual_amount,
        counterfactual=r.counterfactual,
        output_summary=v.reason,
        sql=r.sql,
        rows=r.rows,
    )
