"""Critic (sample mode: rule-based, ADR-0007). It applies docs/causal-criteria.md:

- labels each link: breakdown (where in the numbers), causal (passes §2), leverage (a small
  change with a large share), background (a normal condition, rejected);
- measures share explained and residual against the root effect (intervention test, §1);
- decides: accept, prune (send back to the hypothesiser), retry (send back to the analyst with
  a corrected scope), or frontier (the cause probably exists but no registered source can test
  it). It can block: nothing reaches the report without its verdict.
- escalates borderline calls to the critic-escalation gate (ADR-0006) with a proposal.
- judges a recalled lesson (``judge_lesson``, causal-criteria.md §6): the step it suggested is
  tested like any other candidate; if the critic prunes it, the lesson is rejected for the run
  (negative feedback to H-MEM), so a lesson that does not fit never shapes the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .common import (
    ESCALATION_MARGIN,
    LEVERAGE_MAX_RELATIVE_CHANGE,
    MIN_SHARE,
    MOST_OF_EFFECT,
    OVER_EXPLAINED,
    Candidate,
    Frame,
    Measurement,
    Node,
    Verdict,
)
from .knowledge import money, pct

AGENT = "critic"
BREAKDOWN_RULES = frozenset({"component", "concept_switch"})


def judge(candidate: Candidate, m: Measurement, frame: Frame, *, retries_left: int) -> Verdict:
    """One verdict for one measurement. Pure: no I/O, so it is unit-testable on its own."""
    root = frame.delta
    intended = "breakdown" if candidate.rule in BREAKDOWN_RULES else "causal"
    if m.error:
        return Verdict("prune", intended, f"not testable: {m.error}", "hypothesis")

    # Temporal test (§2.2): events must fall in the window in which the effect arose.
    if candidate.rule == "filing_event" and m.scope == "broad":
        if not m.events:
            return Verdict(
                "prune", intended, "no such filing event in the registered sources", "hypothesis"
            )
        after, until = frame.window()
        if retries_left > 0:
            return Verdict(
                "retry",
                "causal",
                f"temporal test: {m.events_outside_window} of {len(m.events)} events fall outside "
                f"the window ({after}, {until}] in which the effect arose; retry restricted to it",
                retry_scope="window",
            )
        return Verdict(
            "prune", intended, "events not restricted to the window; retries spent", "hypothesis"
        )

    if candidate.rule == "comparative_restated":
        if not m.mechanism:
            return Verdict("prune", "background", "the comparative was not restated")
        return Verdict(
            "prune",
            "background",
            "a restated comparative is a normal reporting condition, not a cause; the change is "
            "already measured like-for-like against the comparative in the target filing",
            "evidenced",
        )

    if m.effect is None:
        if m.events and not m.testable:
            # Mechanism found, magnitude not testable with registered sources.
            status = "partially-evidenced" if m.mechanism else "hypothesis"
            return Verdict(
                "frontier",
                "causal",
                f"{len(m.events)} event(s) in the window"
                + (f" and {'; '.join(m.mechanism)}" if m.mechanism else "")
                + f", but no registered source shows {m.missing}: the intervention test "
                "cannot be run",
                status,
                escalation={
                    "question": "Cause is plausible but its share cannot be measured with the "
                    "registered sources. Hand it to Loop B as an information need, or prune it?",
                    "options": ("frontier", "prune"),
                    "proposal": "frontier",
                },
            )
        if m.missing and m.scope != "relaxed" and m.testable and retries_left > 0:
            return Verdict(
                "retry",
                intended,
                f"{m.missing} not in the same filing; retry with the latest filing for each "
                "period (flagged not like-for-like)",
                retry_scope="relaxed",
            )
        return Verdict("prune", intended, "no evidence in the registered sources", "hypothesis")

    raw = _raw(m.effect, root)
    share = _share(m.effect, root)
    if candidate.rule in BREAKDOWN_RULES:
        if raw is not None and raw >= MIN_SHARE:
            return Verdict(
                "accept",
                "breakdown",
                f"locates {pct(share)} of the {money(root)} effect (an identity, not a cause; "
                "not counted as a level)",
                "evidenced",
                share,
                raw,
            )
        return Verdict(
            "prune",
            "breakdown",
            f"locates only {pct(raw)} of the effect (below {pct(MIN_SHARE)})",
            "evidenced",
            share,
            raw,
        )

    # Causal candidates: abnormal change (§1) and intervention test.
    if m.variable_baseline is not None and m.variable_target == m.variable_baseline:
        return Verdict("prune", "background", "no abnormal change against the baseline")
    if raw is None or raw < MIN_SHARE:
        return Verdict(
            "prune",
            "causal",
            f"intervention removes only {pct(raw)} of the effect (below {pct(MIN_SHARE)})",
            "evidenced",
            share,
            raw,
        )
    label = "causal"
    rel = _relative(m)
    if rel is not None and rel < LEVERAGE_MAX_RELATIVE_CHANGE:
        label = "leverage"
    reason = (
        f"intervention: resetting it to baseline moves the metric to {money(m.counterfactual)} "
        f"and removes {pct(raw)} of the {money(root)} effect"
    )
    if raw > OVER_EXPLAINED:
        return Verdict(
            "accept",
            label,
            reason + "; it over-explains, so offsetting factors exist",
            "evidenced",
            share,
            raw,
            escalation={
                "question": f"Candidate explains {pct(raw)} of the effect (offsets of "
                f"{money(m.effect - root)}). Accept with share capped at 100%, or prune?",
                "options": ("accept-capped", "prune"),
                "proposal": "accept-capped",
            },
        )
    if abs(raw - MOST_OF_EFFECT) < ESCALATION_MARGIN:
        proposal = "accept" if raw >= MOST_OF_EFFECT else "accept-partial"
        return Verdict(
            "accept",
            label,
            reason + "; borderline against 'most of the effect'",
            "evidenced" if raw >= MOST_OF_EFFECT else "partially-evidenced",
            share,
            raw,
            escalation={
                "question": f"Share {pct(raw)} is within {pct(ESCALATION_MARGIN)} of "
                f"{pct(MOST_OF_EFFECT)}. Accept as the main cause, accept as partial, or prune?",
                "options": ("accept", "accept-partial", "prune"),
                "proposal": proposal,
            },
        )
    return Verdict(
        "accept",
        label,
        reason,
        "evidenced" if raw >= MOST_OF_EFFECT else "partially-evidenced",
        share,
        raw,
    )


def apply_gate(verdict: Verdict, choice: str) -> Verdict:
    """Fold the gate's decision into the verdict."""
    if choice == "prune":
        return Verdict(
            "prune",
            verdict.label,
            verdict.reason + "; pruned at the escalation gate",
            verdict.evidence_status,
            verdict.share,
            verdict.raw_share,
        )
    if choice == "accept-partial":
        verdict.evidence_status = "partially-evidenced"
    return verdict


def _raw(effect: float | None, root: float) -> float | None:
    if effect is None or root == 0:
        return None
    return effect / root


def _share(effect: float | None, root: float) -> float | None:
    raw = _raw(effect, root)
    return None if raw is None else round(max(0.0, min(1.0, raw)), 4)


def _relative(m: Measurement) -> float | None:
    if m.variable_baseline in (None, 0) or m.variable_target is None:
        return None
    return abs(m.variable_target - m.variable_baseline) / abs(m.variable_baseline)


def trace_verdict(tracer: Any, candidate: Candidate, m: Measurement, v: Verdict) -> None:
    target = {
        "retry": "analyst",
        "prune": "hypothesiser",
        "accept": "orchestrator",
        "frontier": "gap_analyst",
    }[v.decision]
    tracer.emit(
        agent=AGENT,
        action=v.decision,
        status="rejected" if v.decision == "prune" else "success",
        node_id=candidate.node_id,
        target_agent=target,
        label=v.label,
        evidence_status=v.evidence_status,
        share_explained=v.share,
        residual=None if v.share is None else round(1 - v.share, 4),
        retry_count=m.attempt,
        output_summary=v.reason,
        sql=m.primary_sql,
        rows=m.rows,
    )


# --- recalled lessons (causal-criteria.md §6) ---------------------------------------------------


@dataclass(frozen=True)
class LessonVerdict:
    """The critic's call on a recalled lesson, once the step it suggested has been tested."""

    decision: Literal["support", "reject"]
    feedback: Literal["wrong", "not_helpful"] | None  # sent now for a reject; None for support
    conclusive: bool  # the step held as the lesson recorded it: the level's other candidates wait
    reason: str


def judge_lesson(node: Node, *, expected: str, exact: bool, fits: bool) -> LessonVerdict:
    """Judge a lesson from the tested node of the step it suggested.

    ``expected``: the step's recorded outcome (accepted or frontier); ``exact``: the node is the
    lesson's own step (not an equivalent one); ``fits``: the lesson was learnt on the same kind
    of effect and metric family. Pruned means the lesson does not hold here: rejected, feedback
    ``wrong`` if it was learnt on this kind of question, ``not_helpful`` if it was misapplied.
    Conclusive (the remaining candidates of the level are not tested unless the run later fails)
    only for the lesson's own step, on a fitting question, with the recorded outcome, and, for an
    accepted step, most of the effect explained."""
    if node.verdict == "pruned":
        return LessonVerdict(
            "reject",
            "wrong" if fits else "not_helpful",
            False,
            f"the lesson's step ({node.rule} {node.variable}) was pruned here: {node.reason}"
            + ("" if fits else "; the lesson was learnt on another kind of question"),
        )
    held = node.verdict == expected
    strong = node.verdict == "frontier" or (node.raw_share or 0) >= MOST_OF_EFFECT
    conclusive = bool(exact and fits and held and strong)
    reason = f"the lesson's step ({node.rule} {node.variable}) is {node.verdict} here"
    if conclusive:
        reason += " as the lesson recorded; the level's other candidates are not tested"
    return LessonVerdict("support", None, conclusive, reason)


def trace_lesson(tracer: Any, node: Node, memory_id: str, v: LessonVerdict) -> None:
    tracer.emit(
        agent=AGENT,
        action="lesson_" + v.decision,
        status="rejected" if v.decision == "reject" else "success",
        node_id=node.node_id,
        memory_ids=[memory_id],
        target_agent="memory",
        output_summary=v.reason,
        feedback=v.feedback,
        conclusive=v.conclusive,
    )
