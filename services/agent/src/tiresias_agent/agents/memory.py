"""Memory agent: Meterless H-MEM lessons, never answers (ADR-0005, causal-criteria.md §6).

One ``MemoryRun`` per investigation, in mode ``warm`` (use memory) or ``cold`` (no memory call):

- **recall** (after the framer, before the first hypothesis): one H-MEM query built from the
  frame only (kind of effect, metric family, metric), so it is the same for every company. The
  server's relevance threshold (RECALL_THRESHOLD) filters; the recall is broad on purpose and the
  critic, not the similarity score, decides whether a lesson fits.
- **preferences**: each recalled lesson is a path of generic (rule, key) steps; the hypothesiser
  tests the candidates with those steps first. A lesson adds no candidate and no fact, and the
  critic judges every candidate as it always does.
- **observe**: when a tested node is a lesson's step, the critic judges the lesson
  (``critic.judge_lesson``). Pruned: the lesson is rejected for the run and negative feedback goes
  to H-MEM at once. Held as recorded on a fitting question: the level's other candidates wait
  (fewer hops); if the run then fails to reach a cause, the orchestrator re-runs Loop A without
  that shortcut (``fall_back``) and the lesson gets ``not_helpful``.
- **write-back** (memory gate): a run that ends ``actionable`` or ``branched`` distils the path
  that worked into a lesson with no entity, ticker, CIK, amount or date, and stores it behind the
  ``memory`` gate (auto policy: store). A lesson whose path is already in memory is not stored
  again; it gets ``helpful`` feedback instead.

Cognition down: the recall's ``degraded`` trace line (from the client) is the only sign, and the
run goes on without memory (rule 8): no further memory call is made.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..adapters.cognition import Gate as MemoryGate
from ..gates import GateRequest
from ..models import MemoryFeedback as FeedbackItem
from ..models import MemoryMode, RecalledLesson, RunMemory, SkippedCandidate, StoredLesson
from .common import Candidate, Frame, Node
from .critic import LessonVerdict, judge_lesson, trace_lesson
from .knowledge import (
    LESSON_KIND_TEXT,
    LESSON_STEP_TEXT,
    STEP_EQUIVALENTS,
    family,
    lesson_event_text,
    step_key,
)

AGENT = "memory"
DEFAULT_NAMESPACE = "lessons"
MODES: tuple[str, ...] = ("warm", "cold")
SUCCESS_STATES = frozenset({"actionable", "branched", "external-driver"})
STORE_STATES = frozenset({"actionable", "branched"})
OUTCOMES = frozenset({"accepted", "frontier"})
LESSON_CONFIDENCE = 0.7
PATH_TAG = re.compile(r"^path:([a-z_]+)=([A-Za-z0-9_]+)>(accepted|frontier)$")


def resolve_mode(asked: str | None) -> tuple[str, str]:
    """(mode, why) for a run. MEMORY_MODE, read at call time: ``warm`` (the default) or ``cold``
    is the default when the request names none (RunOptions.memory); ``off`` is the operator
    override, cold whatever the request asks. Anything else counts as warm."""
    env = os.getenv("MEMORY_MODE", "warm").strip().lower()
    if env == "off":
        return "cold", "MEMORY_MODE=off: memory is off for every run"
    if asked in MODES:
        return asked, f"options.memory={asked}"
    mode = env if env in MODES else "warm"
    return mode, f"MEMORY_MODE default ({mode})"


def namespace() -> str:
    return os.getenv("MEMORY_NAMESPACE", "").strip() or DEFAULT_NAMESPACE


def recall_query(frame: Frame) -> str:
    """The recall query: generic words from the frame, no entity (so lessons transfer)."""
    fam = family(frame.metric)
    article = "an" if fam[:1] in "aeiou" else "a"
    return (
        f"Lesson: why a filed total moved in annual reports, {article} {fam} total "
        f"{LESSON_KIND_TEXT.get(frame.kind, frame.kind)}"
    )


@dataclass(frozen=True)
class Step:
    rule: str
    key: str
    outcome: str  # accepted | frontier

    @property
    def tag(self) -> str:
        return f"path:{self.rule}={self.key}>{self.outcome}"


@dataclass
class Lesson:
    memory_id: str
    content: str
    relevance: float | None
    confidence: float
    kind: str | None
    family: str | None
    steps: tuple[Step, ...]
    verdicts: list[tuple[str, LessonVerdict]] = field(default_factory=list)  # (node id, verdict)

    @property
    def rejected(self) -> bool:
        return any(v.decision == "reject" for _, v in self.verdicts)

    @property
    def status(self) -> str:
        if not self.steps:
            return "ignored"
        if self.rejected:
            return "rejected"
        return "supported" if self.verdicts else "not-tested"

    def fits(self, frame: Frame) -> bool:
        return self.kind == frame.kind and self.family == family(frame.metric)

    def signature(self) -> tuple:
        return (self.kind, self.family, tuple(s.tag for s in self.steps))


def parse(lesson: Any) -> Lesson:
    """A recalled H-MEM lesson as steps. One without path tags is kept but never applied."""
    tags = list(lesson.tags or [])
    steps = tuple(
        Step(m.group(1), m.group(2), m.group(3)) for t in tags if (m := PATH_TAG.match(str(t)))
    )
    kind = next((t.split(":", 1)[1] for t in tags if str(t).startswith("kind:")), None)
    fam = next((t.split(":", 1)[1] for t in tags if str(t).startswith("family:")), None)
    return Lesson(
        memory_id=lesson.id,
        content=lesson.content,
        relevance=lesson.relevance,
        confidence=lesson.confidence,
        kind=kind,
        family=fam,
        steps=steps,
    )


def _node_step(node: Node) -> tuple[str, str] | None:
    if not node.rule:
        return None
    return node.rule, step_key(node.rule, node.variable)


def path_nodes(nodes: Sequence[Node], chosen: Node) -> list[Node]:
    """The chosen node and its ancestors below the root, root side first."""
    by_id = {n.node_id: n for n in nodes}
    path, node = [], chosen
    while node is not None and node.parent_id is not None:
        path.append(node)
        node = by_id.get(node.parent_id)
    return list(reversed(path))


def distil(frame: Frame, nodes: Sequence[Node], chosen: Sequence[Node], end_state: str):
    """(content, tags) of the lesson a successful run teaches, or None. Generic by construction:
    built from the rule vocabulary (knowledge.py), the kind of effect and the metric family."""
    if not chosen:
        return None
    path = path_nodes(nodes, chosen[0])
    steps: list[Step] = []
    words: list[str] = []
    for node in path:
        key = _node_step(node)
        if key is None or node.verdict not in OUTCOMES:
            return None
        rule, k = key
        step = Step(rule, k, node.verdict)
        if not PATH_TAG.match(step.tag):
            return None
        steps.append(step)
        text = LESSON_STEP_TEXT.get((rule, node.verdict), "{rule} step {concept} was {verdict}")
        words.append(
            text.format(
                event=lesson_event_text(rule, k), concept=k, rule=rule, verdict=node.verdict
            )
        )
    if not steps:
        return None
    fam = family(frame.metric)
    content = (
        f"Lesson: why a filed {fam} total {LESSON_KIND_TEXT.get(frame.kind, frame.kind)}. "
        f"Path that worked: {'; then '.join(words)}. Ended {end_state}."
    )
    tags = [f"kind:{frame.kind}", f"family:{fam}", *(s.tag for s in steps), f"end:{end_state}"]
    return content, tags


def entity_literals(frame: Frame, nodes: Sequence[Node]) -> list[str]:
    """Words a lesson must never contain: the run's ticker, CIK and counterparty names."""
    out = [frame.ticker, frame.cik or ""]
    if frame.cik:
        out.append(frame.cik.lstrip("0"))
    for n in nodes:
        if n.rule == "reconcile" and " of " in n.variable:
            out.append(n.variable.split(" of ", 1)[1])
    return [w for w in out if w and len(w) >= 2]


def entity_free(text: str, literals: Sequence[str]) -> str | None:
    """None if the text is generic, else why not (amounts, long numbers, dates, entity words)."""
    if "$" in text:
        return "contains an amount"
    if re.search(r"\d{4,}", text):
        return "contains a long number (a CIK, an amount or a date)"
    for word in literals:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            return "contains an entity literal"
    return None


class MemoryRun:
    """Memory for one run (see the module docstring)."""

    def __init__(
        self,
        mode: MemoryMode | str,
        *,
        client: Any | None,
        tracer: Any,
        gate: Any | None = None,
        namespace_: str | None = None,
    ) -> None:
        self.mode = mode if mode in MODES else "warm"
        self.client = client if self.mode == "warm" else None
        self.tracer = tracer
        self.gate = gate
        self.namespace = namespace_ or namespace()
        self.available: bool | None = None
        self.query: str | None = None
        self.lessons: list[Lesson] = []
        self.skipped: list[SkippedCandidate] = []
        self.feedback_sent: list[FeedbackItem] = []
        self.stored: StoredLesson | None = None
        self.fallback = False
        self.shortcuts = True
        self.note: str | None = None
        self.gate_decisions = 0

    @property
    def on(self) -> bool:
        return self.mode == "warm" and self.client is not None and self.available is not False

    # --- recall -------------------------------------------------------------------------------

    def announce_cold(self, why: str) -> None:
        self.note = f"cold ({why}): no recall, no write-back, no feedback"
        self.tracer.emit(agent=AGENT, action="off", output_summary=self.note)

    def recall(self, frame: Frame) -> None:
        if self.mode == "cold" or self.client is None:
            return
        self.query = recall_query(frame)
        result = self.client.recall(self.query, namespace=self.namespace)
        self.available = bool(result.available)
        if not result.available:
            self.note = f"cognition unavailable, run without memory: {result.error}"[:300]
            self.client = None  # one degraded line (the client's), then no more memory calls
            return
        self.lessons = [parse(item) for item in result.lessons]
        scores = {item.memory_id: item.relevance for item in self.lessons}
        applied = [item.memory_id for item in self.lessons if item.steps]
        self.tracer.emit(
            agent=AGENT,
            action="recall",
            node_id="n0",
            target_agent="hypothesiser",
            memory_ids=list(scores),
            scores=scores,
            applied=applied,
            input_summary=self.query,
            output_summary=f"{len(self.lessons)} lesson(s) recalled, {len(applied)} with a path "
            "the hypothesiser can test first"
            if self.lessons
            else "no lesson above the recall threshold",
        )

    def preferences(self) -> list[tuple[str, str, str]]:
        """(rule, key, memory id) steps of the active lessons, best lesson first."""
        out: list[tuple[str, str, str]] = []
        for lesson in self.lessons:
            if lesson.rejected:
                continue
            for step in lesson.steps:
                out.append((step.rule, step.key, lesson.memory_id))
                for rule, key in STEP_EQUIVALENTS.get((step.rule, step.key), ()):
                    out.append((rule, key, lesson.memory_id))
        return out

    # --- the critic's call on a lesson ----------------------------------------------------------

    def observe(self, node: Node, frame: Frame) -> bool:
        """Judge every active lesson whose step this tested node is. True if one is conclusive
        (the level's other candidates can wait). Negative feedback is sent at once."""
        if not self.lessons:
            return False
        key = _node_step(node)
        if key is None or node.verdict == "root":
            return False
        conclusive = False
        for lesson in self.lessons:
            if lesson.rejected or not lesson.steps:
                continue
            match = self._match(lesson, key)
            if match is None:
                continue
            step, exact = match
            if any(nid == node.node_id for nid, _ in lesson.verdicts):
                continue
            v = judge_lesson(node, expected=step.outcome, exact=exact, fits=lesson.fits(frame))
            if not self.shortcuts and v.conclusive:
                v = LessonVerdict(v.decision, v.feedback, False, v.reason + " (shortcut off)")
            lesson.verdicts.append((node.node_id, v))
            trace_lesson(self.tracer, node, lesson.memory_id, v)
            if v.decision == "reject" and v.feedback:
                self._feedback(lesson.memory_id, v.feedback, v.reason)
            conclusive = conclusive or v.conclusive
        return conclusive

    @staticmethod
    def _match(lesson: Lesson, key: tuple[str, str]) -> tuple[Step, bool] | None:
        for step in lesson.steps:
            if (step.rule, step.key) == key:
                return step, True
        for step in lesson.steps:
            if key in STEP_EQUIVALENTS.get((step.rule, step.key), ()):
                return step, False
        return None

    def skip(self, candidates: Sequence[Candidate], node: Node) -> None:
        """The level's remaining candidates, not tested because ``node`` held as a lesson said."""
        memory_id = next(
            (
                lsn.memory_id
                for lsn in self.lessons
                for nid, v in lsn.verdicts
                if nid == node.node_id and v.conclusive
            ),
            None,
        )
        for cand in candidates:
            self.skipped.append(
                SkippedCandidate(
                    node_id=cand.node_id,
                    rule=cand.rule,
                    variable=cand.variable,
                    claim=cand.claim,
                    memory_id=memory_id,
                )
            )
        self.tracer.emit(
            agent=AGENT,
            action="skip",
            node_id=node.node_id,
            target_agent="orchestrator",
            memory_ids=[memory_id] if memory_id else [],
            skipped=[c.node_id for c in candidates],
            output_summary=f"{node.node_id} held as the recalled lesson recorded; not tested: "
            + ", ".join(c.node_id for c in candidates),
        )

    def fall_back(self, end_state: str | None) -> None:
        """The shortcut did not lead to a cause: Loop A runs again testing every candidate."""
        self.fallback, self.shortcuts = True, False
        shortcut = {s.memory_id for s in self.skipped if s.memory_id}
        self.tracer.emit(
            agent=AGENT,
            action="fallback",
            status="rejected",
            memory_ids=sorted(shortcut),
            target_agent="orchestrator",
            output_summary=f"the lesson's shortcut ended {end_state}; Loop A runs again and "
            "tests every candidate",
        )
        for memory_id in sorted(shortcut):
            self._feedback(memory_id, "not_helpful", f"its shortcut ended {end_state}")

    # --- feedback and write-back ----------------------------------------------------------------

    def _feedback(self, memory_id: str, kind: str, reason: str) -> None:
        if self.client is None or any(f.memory_id == memory_id for f in self.feedback_sent):
            return
        result = self.client.feedback(memory_id, kind)
        self.feedback_sent.append(
            FeedbackItem(memory_id=memory_id, kind=kind, applied=result.applied, reason=reason)
        )
        self.tracer.emit(
            agent=AGENT,
            action="feedback",
            status="success" if result.applied else "degraded",
            memory_ids=[memory_id],
            feedback=kind,
            output_summary=f"{kind}: {reason}"[:300],
        )

    def finish(
        self,
        *,
        frame: Frame | None,
        nodes: Sequence[Node],
        chosen: Sequence[Node],
        end_state: str | None,
        error: bool,
        scoped: bool,
    ) -> None:
        """Positive feedback for the lessons that held, then the write-back."""
        if not self.on:
            return
        ok = not error and end_state in SUCCESS_STATES
        for lesson in self.lessons:
            if lesson.status == "supported" and ok:
                self._feedback(lesson.memory_id, "helpful", f"its path led to {end_state}")
        if error or scoped or frame is None or end_state not in STORE_STATES:
            self.tracer.emit(
                agent=AGENT,
                action="remember",
                status="rejected",
                output_summary=f"no lesson: the run ended {end_state or 'in error'} (lessons "
                "come from actionable or branched runs only)",
            )
            return
        self._remember(frame, nodes, chosen, end_state)

    def _remember(
        self, frame: Frame, nodes: Sequence[Node], chosen: Sequence[Node], end_state: str
    ) -> None:
        distilled = distil(frame, nodes, chosen, end_state)
        if distilled is None:
            self._not_stored("", [], "no lesson: the chosen path has no generic step")
            return
        content, tags = distilled
        why = entity_free(content + " " + " ".join(tags), entity_literals(frame, nodes))
        if why:
            self._not_stored(content, tags, f"not stored: the lesson {why}")
            return
        signature = (tags[0].split(":", 1)[1], tags[1].split(":", 1)[1])
        steps = tuple(t for t in tags if t.startswith("path:"))
        same = next(
            (
                lsn
                for lsn in self.lessons
                if lsn.signature() == (*signature, steps) and not lsn.rejected
            ),
            None,
        )
        if same is None:  # not recalled (below the threshold): look in the namespace itself
            same = self._existing((*signature, steps))
        if same is not None:
            self._not_stored(
                content,
                tags,
                f"same lesson already in memory ({same.memory_id}); not stored again",
                memory_id=same.memory_id,
            )
            return
        choice, mode, decided_by, reason = "store", "auto", "policy:memory", "auto policy"
        if self.gate is not None:
            decision = self.gate.decide(
                GateRequest(
                    gate="memory",
                    question=f"Store this lesson in H-MEM? {content}",
                    options=("store", "discard"),
                    proposal="store",
                    node_id=chosen[0].node_id if chosen else None,
                    requested_by=AGENT,
                    context={"tags": tags},
                )
            )
            self.gate_decisions += 1
            choice, mode = decision.choice, decision.mode
            decided_by, reason = decision.decided_by, decision.reason
        gate = MemoryGate(
            mode=mode,
            decision="approve" if choice == "store" else "reject",
            decided_by=decided_by,
            reason=reason[:200],
        )
        assert self.client is not None
        result = self.client.remember(
            content,
            namespace=self.namespace,
            agent_id=AGENT,
            tags=tags,
            confidence=LESSON_CONFIDENCE,
            gate=gate,
        )
        memory_id = result.lesson.id if result.lesson else None
        if result.stored:
            why = f"stored behind the memory gate ({decided_by})"
        elif choice != "store":
            why = f"discarded at the memory gate ({decided_by}); the ledger records the rejection"
        else:
            why = f"not stored: {result.error or 'rejected by cognition'}"
        self.stored = StoredLesson(
            stored=bool(result.stored),
            memory_id=memory_id,
            content=content,
            tags=tags,
            gate_choice=choice,
            decided_by=decided_by,
            reason=why,
        )
        self.tracer.emit(
            agent=AGENT,
            action="remember",
            status="success"
            if result.stored
            else ("rejected" if choice != "store" else "degraded"),
            memory_ids=[memory_id] if memory_id else [],
            gate="memory",
            decided_by=decided_by,
            input_summary=content[:300],
            output_summary=why,
        )

    def _existing(self, signature: tuple) -> Lesson | None:
        """A stored lesson with the same path, so it is not stored twice. A failed listing
        means unknown: the lesson is stored (the gate still decides)."""
        assert self.client is not None
        try:
            stored = self.client.list_memory(namespace=self.namespace)
        except Exception:  # noqa: BLE001 - CognitionError: the client traced it
            return None
        return next((lsn for lsn in map(parse, stored) if lsn.signature() == signature), None)

    def _not_stored(
        self, content: str, tags: list[str], why: str, memory_id: str | None = None
    ) -> None:
        self.stored = StoredLesson(
            stored=False,
            memory_id=memory_id,
            content=content,
            tags=tags,
            gate_choice=None,
            decided_by=None,
            reason=why,
        )
        self.tracer.emit(
            agent=AGENT,
            action="remember",
            status="rejected",
            memory_ids=[memory_id] if memory_id else [],
            input_summary=content[:300],
            output_summary=why,
        )

    # --- report ---------------------------------------------------------------------------------

    def report(self) -> RunMemory:
        recalled = [
            RecalledLesson(
                memory_id=lsn.memory_id,
                content=lsn.content,
                relevance=lsn.relevance,
                confidence=lsn.confidence,
                status=lsn.status,
                node_ids=[nid for nid, _ in lsn.verdicts],
                reason="; ".join(v.reason for _, v in lsn.verdicts) or None,
            )
            for lsn in self.lessons
        ]
        return RunMemory(
            mode=self.mode,
            available=self.available,
            namespace=self.namespace if self.mode == "warm" else None,
            query=self.query,
            recalled=recalled,
            rejected=[r.memory_id for r in recalled if r.status == "rejected"],
            skipped=list(self.skipped),
            fallback=self.fallback,
            feedback=list(self.feedback_sent),
            stored=self.stored,
            note=self.note,
        )
