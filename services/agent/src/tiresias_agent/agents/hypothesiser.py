"""Hypothesiser (sample mode: rule-based, ADR-0007).

Proposes up to MAX_BRANCHES_PER_LEVEL candidate causes for one node, from generic rules:
- hierarchy children of the node's variable (accounting identities -> breakdown candidates);
- event concepts that roll into the node's variable (impairments -> causal candidates);
- 8-K item codes that can move the number (2.01 disposal, 4.02 non-reliance);
- for a restatement: a switch of the reported concept between the two filings;
- for a change: whether the prior-period comparative was itself restated (like-for-like check).
A candidate whose variable is already on the path from the root is dropped (no cycles).
"""

from __future__ import annotations

from typing import Any

from .common import Candidate, Frame, Node
from .knowledge import (
    COMPONENTS,
    DISCONTINUED_OPS,
    EVENT_CONCEPTS,
    FILING_EVENTS,
    family,
    money,
)

AGENT = "hypothesiser"


def propose(
    frame: Frame,
    parent: Node,
    path_variables: set[str],
    *,
    max_branches: int,
    tracer: Any,
    exclude: set[str] | None = None,
) -> list[Candidate]:
    """Candidates for the next level under ``parent``, best first, capped at max_branches."""
    exclude = exclude or set()
    fam = family(frame.metric)
    var = parent.variable
    at_root = parent.parent_id is None
    raw: list[tuple[str, str, str, str, dict[str, Any]]] = []  # rule, variable, claim, category

    for comp in COMPONENTS.get(var, ()):
        raw.append(
            (
                "component",
                comp,
                f"{comp} accounts for the change in {var}",
                "location",
                {"concept": comp},
            )
        )
    for ev in EVENT_CONCEPTS:
        if var in ev.rolls_into or (at_root and var not in COMPONENTS and fam == "income"):
            raw.append(
                (
                    "event_concept",
                    ev.concept,
                    f"An abnormal {ev.description} drives the change in {var}",
                    ev.category,
                    {"concept": ev.concept, "sign": ev.sign, "description": ev.description},
                )
            )
    for fe in FILING_EVENTS:
        if frame.kind not in fe.modes or fam not in fe.families:
            continue
        # A disposal reaches an income total through discontinued operations; at the root of an
        # income metric it is tested under that component instead.
        if fe.key == "disposal" and fam == "income" and at_root and var in COMPONENTS:
            continue
        if not at_root and not (fe.key == "disposal" and var == DISCONTINUED_OPS):
            continue
        raw.append(
            (
                "filing_event",
                f"8-K {'/'.join(fe.items)}",
                f"A {fe.description} between the two reports changed {frame.metric}"
                if frame.kind == "restatement"
                else f"A {fe.description} during the period changed {var}",
                fe.category,
                {"event": fe.key},
            )
        )
    if at_root and frame.kind == "restatement":
        raw.append(
            (
                "concept_switch",
                f"concept:{frame.metric}",
                "The two filings report the figure under different concepts (a definition change)",
                "accounting",
                {},
            )
        )
    if at_root and frame.kind == "change":
        raw.append(
            (
                "comparative_restated",
                f"comparative:{frame.metric}",
                f"The prior-period {frame.metric} was itself restated, so the baseline moved",
                "location",
                {},
            )
        )

    candidates: list[Candidate] = []
    dropped: list[str] = []
    for rule, variable, claim, category, params in raw:
        if variable in path_variables or variable in exclude:
            dropped.append(variable)
            continue
        if len(candidates) >= max_branches:
            dropped.append(variable)
            continue
        candidates.append(
            Candidate(
                node_id=f"{parent.node_id}.{len(candidates) + 1}",
                parent_id=parent.node_id,
                depth=parent.depth + 1,
                rule=rule,
                variable=variable,
                claim=claim,
                category=category,
                params=params,
            )
        )
    for c in candidates:
        tracer.emit(
            agent=AGENT,
            action="propose",
            node_id=c.node_id,
            parent_node_id=c.parent_id,
            target_agent="analyst",
            input_summary=f"explain {parent.variable} ({money(parent.effect)})",
            output_summary=c.claim,
            rule=c.rule,
            depth=c.depth,
        )
    if dropped:
        tracer.emit(
            agent=AGENT,
            action="cap",
            node_id=parent.node_id,
            output_summary=f"not proposed (cap {max_branches}, on path, or already tested): "
            + ", ".join(dropped),
        )
    return candidates
