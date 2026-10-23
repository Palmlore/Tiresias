"""Human-in-the-loop gates (ADR-0006): one interface for every gate.

A gate takes a ``GateRequest`` (which gate, the question, the allowed options and the
requesting agent's proposal) and returns a ``GateDecision``. Modes:
- ``auto`` (default, what judges run): a policy decides and the decision is logged;
- ``scripted``: the decision comes from ``RunOptions.decisions`` keyed by "<gate>:<node_id>"
  or "<gate>"; a missing or invalid entry falls back to the policy, and the trace says so;
- ``interactive``: an ``ask`` callable decides (a CLI prompt, later the UI); without one it
  falls back to the policy.
Every decision is one trace line with gate, gate_mode and decided_by. The source and memory
gates (Loop B, H-MEM) reuse this class with their own policies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

AGENT = "gate"
GATES = ("framing", "source", "critic_escalation", "memory")
MODES = ("auto", "interactive", "scripted")


@dataclass(frozen=True)
class GateRequest:
    gate: str
    question: str
    options: tuple[str, ...]
    proposal: str
    node_id: str | None = None
    requested_by: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    gate: str
    choice: str
    mode: str
    decided_by: str
    reason: str


Policy = Callable[[GateRequest], tuple[str, str]]  # -> (choice, reason)


def follow_proposal(request: GateRequest) -> tuple[str, str]:
    """Default auto policy: take the requesting agent's own conservative proposal."""
    return request.proposal, "auto policy: follow the proposal of " + (
        request.requested_by or "the requesting agent"
    )


class Gate:
    def __init__(
        self,
        mode: str = "auto",
        *,
        tracer: Any,
        policies: Mapping[str, Policy] | None = None,
        scripted: Mapping[str, Any] | None = None,
        ask: Callable[[GateRequest], str] | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"gate mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.tracer = tracer
        self.policies = dict(policies or {})
        self.scripted = dict(scripted or {})
        self.ask = ask

    def _policy(self, request: GateRequest) -> tuple[str, str]:
        choice, reason = self.policies.get(request.gate, follow_proposal)(request)
        if choice not in request.options:
            return request.proposal, f"policy returned {choice!r}, not an option; used proposal"
        return choice, reason

    def decide(self, request: GateRequest) -> GateDecision:
        if request.proposal not in request.options:
            raise ValueError(f"proposal {request.proposal!r} is not one of {request.options}")
        decided_by, choice, reason = f"policy:{request.gate}", None, ""
        if self.mode == "scripted":
            key = f"{request.gate}:{request.node_id}"
            scripted = self.scripted.get(key, self.scripted.get(request.gate))
            if scripted in request.options:
                choice, decided_by, reason = scripted, "script", f"scripted decision ({key})"
            else:
                reason = "no valid scripted decision; "
        elif self.mode == "interactive":
            if self.ask is not None:
                answer = self.ask(request)
                if answer in request.options:
                    choice, decided_by, reason = answer, "human", "interactive answer"
                else:
                    reason = f"answer {answer!r} not an option; "
            else:
                reason = "no interactive channel; "
        if choice is None:
            choice, why = self._policy(request)
            reason += why
        decision = GateDecision(request.gate, choice, self.mode, decided_by, reason)
        self.tracer.emit(
            agent=AGENT,
            action="decide",
            status="rejected" if choice == "prune" else "success",
            node_id=request.node_id,
            gate=request.gate,
            gate_mode=self.mode,
            decided_by=decided_by,
            target_agent=request.requested_by or None,
            input_summary=request.question,
            output_summary=f"{choice} ({reason})",
            options=list(request.options),
            proposal=request.proposal,
        )
        return decision
