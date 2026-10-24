"""Human-in-the-loop gates (ADR-0006): one interface for every gate.

A gate takes a ``GateRequest`` (which gate, the question, the allowed options and the
requesting agent's proposal) and returns a ``GateDecision``. Modes:
- ``auto`` (default, what judges run): a policy decides and the decision is logged;
- ``scripted``: the decision comes from ``RunOptions.decisions`` keyed by "<gate>:<node_id>"
  or "<gate>"; a missing or invalid entry falls back to the policy, and the trace says so;
- ``interactive``: an ``ask`` callable decides: ``GateChannel.ask`` over the HTTP API (the run
  waits for ``POST /runs/{id}/gates/{gate_id}``, at most GATE_TIMEOUT_SECONDS and never beyond
  the run budget left; no answer applies the policy with ``decided_by: "timeout"``) or a stdin
  prompt in the CLI (``--interactive``); without one it falls back to the policy.
Every decision is one trace line with gate, gate_mode and decided_by. The source and memory
gates (Loop B, H-MEM) reuse this class with their own policies.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import gate_timeout_seconds
from .trace import REDACTED, redact

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
    note: str | None = None  # interactive: the human's note
    gate_id: str | None = None  # interactive over HTTP: the GateChannel id


@dataclass(frozen=True)
class Answer:
    """What an ``ask`` callable may return instead of a bare choice. ``decided_by="timeout"``
    means nobody answered in time: the gate applies the policy."""

    choice: str | None
    note: str | None = None
    gate_id: str | None = None
    decided_by: str = "human"


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
        note: str | None = None
        gate_id: str | None = None
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
                if not isinstance(answer, Answer):
                    answer = Answer(choice=answer)
                note, gate_id = answer.note, answer.gate_id
                if answer.decided_by == "timeout":
                    decided_by, reason = "timeout", "no answer before the gate deadline; "
                elif answer.choice in request.options:
                    choice, decided_by = answer.choice, answer.decided_by
                    reason = "interactive answer"
                else:
                    reason = f"answer {answer.choice!r} not an option; "
            else:
                reason = "no interactive channel; "
        if choice is None:
            choice, why = self._policy(request)
            reason += why
        decision = GateDecision(
            request.gate, choice, self.mode, decided_by, reason, note=note, gate_id=gate_id
        )
        extra: dict[str, Any] = {}
        if self.mode == "interactive":  # auto and scripted lines stay exactly as before
            extra = {"choice": choice, "note": note, "gate_id": gate_id}
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
            **extra,
        )
        return decision


# --- interactive gates over HTTP (runs.py, app.py) -------------------------------------------

MAX_TEXT = 300  # characters per context string
MAX_ITEMS = 20  # keys per mapping, items per list, in a gate's context
MAX_NOTE = 500
_SECRET_ENV = re.compile(r"token|key|password|secret|authorization", re.IGNORECASE)


class GateNotFound(Exception):
    """No gate with this id in the run."""


class GateClosed(Exception):
    """The gate was already answered or has expired (the run moved on)."""

    def __init__(self, gate_id: str, status: str) -> None:
        super().__init__(f"gate {gate_id} is {status}")
        self.status = status


class InvalidChoice(Exception):
    """The answer is not one of the gate's options."""


def _secret_values() -> list[str]:
    """Values of secret-looking environment variables (8+ characters): masked in any text a
    gate shows or records."""
    return [v for k, v in os.environ.items() if _SECRET_ENV.search(k) and v and len(v) >= 8]


def scrub(value: Any, *, limit: int = MAX_TEXT, _depth: int = 0, _secrets=None) -> Any:
    """A short, JSON-safe, secret-free copy of a gate's context or note: trace.redact (secret
    keys, bearer tokens), then any secret environment value masked, strings cut to ``limit``,
    mappings and lists to MAX_ITEMS entries, nesting to 3 levels."""
    secrets = _secret_values() if _secrets is None else _secrets
    if _depth == 0:
        value = redact(value)
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value[:limit]
    if value is None or isinstance(value, bool | int | float):
        return value
    nested = {"limit": limit, "_depth": _depth + 1, "_secrets": secrets}
    if _depth >= 3:
        return scrub(str(value), **nested)
    if isinstance(value, Mapping):
        return {str(k)[:64]: scrub(v, **nested) for k, v in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, list | tuple | set | frozenset):
        return [scrub(v, **nested) for v in list(value)[:MAX_ITEMS]]
    return scrub(str(value), **nested)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class PendingGate:
    """One interactive gate of a run: waiting, then answered (by a human) or expired."""

    gate_id: str
    request: GateRequest
    context: dict[str, Any]
    created_at: str
    deadline: str
    timeout_seconds: float
    status: str = "waiting"  # waiting | answered | expired
    choice: str | None = None
    note: str | None = None
    decided_by: str | None = None
    answered_at: str | None = None

    def public(self) -> dict[str, Any]:
        """The GateWaiting body (contracts/openapi/agent.yaml)."""
        r = self.request
        return {
            "gate_id": self.gate_id,
            "gate": r.gate,
            "status": self.status,
            "question": r.question,
            "options": list(r.options),
            "proposal": r.proposal,
            "node_id": r.node_id,
            "requested_by": r.requested_by,
            "context": self.context,
            "created_at": self.created_at,
            "deadline": self.deadline,
            "timeout_seconds": self.timeout_seconds,
            "choice": self.choice,
            "note": self.note,
            "decided_by": self.decided_by,
            "answered_at": self.answered_at,
        }


class GateChannel:
    """The HTTP channel for one run's interactive gates.

    ``ask`` runs on the run's own thread: it records the gate, traces ``gate``/``waiting`` and
    blocks that thread (only that one) until ``answer`` is called from the API or the wait ends.
    The wait is GATE_TIMEOUT_SECONDS (read at call time), capped at the run budget left
    (``run_deadline``, on ``clock``); with no answer the gate expires (``gate``/``expired``) and
    the Gate applies its policy with ``decided_by: "timeout"``. Answer and expiry are decided
    under one lock, so a late answer gets GateClosed, never a second decision.
    """

    def __init__(
        self,
        *,
        tracer: Any,
        run_deadline: float | None = None,
        timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tracer = tracer
        self.run_deadline = run_deadline
        self.timeout = timeout
        self.clock = clock
        self._gates: dict[str, PendingGate] = {}
        self._cond = threading.Condition()

    def _wait_seconds(self) -> float:
        wait = self.timeout if self.timeout is not None else gate_timeout_seconds()
        if self.run_deadline is not None:
            wait = min(wait, max(0.0, self.run_deadline - self.clock()))
        return round(wait, 3)

    def ask(self, request: GateRequest) -> Answer:
        wait = self._wait_seconds()
        now = datetime.now(UTC)
        with self._cond:
            gate_id = f"g{len(self._gates) + 1}"
            pending = PendingGate(
                gate_id=gate_id,
                request=request,
                context=scrub(dict(request.context)),
                created_at=_iso(now),
                deadline=_iso(now + timedelta(seconds=wait)),
                timeout_seconds=wait,
            )
            self._gates[gate_id] = pending
            # Traced under the lock, so an answer is never traced before its waiting line.
            self.tracer.emit(
                agent=AGENT,
                action="waiting",
                node_id=request.node_id,
                gate=request.gate,
                gate_mode="interactive",
                gate_id=gate_id,
                question=request.question,
                options=list(request.options),
                proposal=request.proposal,
                requested_by=request.requested_by or None,
                target_agent="human",
                context=pending.context,
                deadline=pending.deadline,
                timeout_seconds=wait,
                input_summary=request.question,
                output_summary=f"waiting for an answer until {pending.deadline} "
                f"(POST /runs/<run_id>/gates/{gate_id})",
            )
            self._cond.wait_for(lambda: pending.status != "waiting", timeout=wait)
            if pending.status != "waiting":
                return Answer(choice=pending.choice, note=pending.note, gate_id=gate_id)
            pending.status, pending.decided_by = "expired", "timeout"
            pending.answered_at = _iso(datetime.now(UTC))
            self.tracer.emit(
                agent=AGENT,
                action="expired",
                status="degraded",
                node_id=request.node_id,
                gate=request.gate,
                gate_mode="interactive",
                gate_id=gate_id,
                decided_by="timeout",
                proposal=request.proposal,
                output_summary=f"no answer within {wait:g} s; the policy decides",
            )
            return Answer(choice=None, gate_id=gate_id, decided_by="timeout")

    def answer(self, gate_id: str, choice: str, note: str | None = None) -> dict[str, Any]:
        """Answer a waiting gate (from the API thread). Raises GateNotFound, GateClosed
        (answered or expired) or InvalidChoice; returns the answered gate."""
        with self._cond:
            pending = self._gates.get(gate_id)
            if pending is None:
                raise GateNotFound(gate_id)
            if pending.status != "waiting":
                raise GateClosed(gate_id, pending.status)
            if choice not in pending.request.options:
                raise InvalidChoice(
                    f"choice must be one of {list(pending.request.options)}, got {choice!r}"
                )
            note = scrub(note, limit=MAX_NOTE) if note else None
            pending.status, pending.choice, pending.note = "answered", choice, note
            pending.decided_by, pending.answered_at = "human", _iso(datetime.now(UTC))
            self.tracer.emit(
                agent=AGENT,
                action="answered",
                node_id=pending.request.node_id,
                gate=pending.request.gate,
                gate_mode="interactive",
                gate_id=gate_id,
                decided_by="human",
                choice=choice,
                note=note,
                proposal=pending.request.proposal,
                output_summary=f"{choice} (answered over the API)",
            )
            self._cond.notify_all()
            return pending.public()

    def list(self) -> list[dict[str, Any]]:
        with self._cond:
            return [g.public() for g in self._gates.values()]

    def waiting(self) -> dict[str, Any] | None:
        with self._cond:
            return next((g.public() for g in self._gates.values() if g.status == "waiting"), None)
