"""Model lanes through Meterless Nightshift (ADR-0010): the model proposes, the rules and the data
decide.

Three lanes, each one bounded, validated and optional:
- hypothesiser (fast): extra candidate causes for a level, chosen only from the evidence
  vocabulary the source can query (registered concepts, 8-K filing events). A valid proposal
  becomes an ordinary ``Candidate`` (``proposed_by="model"``) that the analyst measures and the
  critic judges like any other; it only fills branch slots the rules left free.
- critic (deep): an independent second opinion (label, accept/prune, a rationale) on the rule
  critic's verdict, from the measurement's own numbers. The rule verdict stays the decision; a
  disagreement goes to the critic_escalation gate, whose auto policy keeps the rule verdict.
- reporter (fast): plain-language wording of the template action. It is used only when it adds
  no number and no name absent from the template; otherwise the template stays.

Which lanes run is the investigation depth (ADR-0011, ``RunOptions.depth``, ``DEPTH_LANES``):
quick (default) = none, rules only; thorough = the critic lane; deep = all three. Precedence, in
``resolve``: sample mode runs no lane at any depth; ``MODEL_LANES=off`` forces quick; otherwise
``RunOptions.depth``, else the deprecated ``RunOptions.model_lanes`` (on = deep, off = quick),
else ``MODEL_LANES=on`` = deep, else quick. A depth with model lanes and no configured Runtime
(or ``MAX_MODEL_CALLS_PER_RUN=0``) runs as quick with a ``degraded`` line. Every call is counted against
``MAX_MODEL_CALLS_PER_RUN`` and the run's wall-clock budget, and is traced by the Nightshift client
as one line (lane, requested and routed model, provider, latency, receipt id). Nightshift routes
on its own (explicit model ids are not honoured), so the routed model comes from the receipt.
``ModelUnavailable`` (offline, timeout, unauthorised) switches the lanes off for the rest of the
run with a ``degraded`` line: the run continues on the rules and never fails because of a model.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..adapters.nightshift import (
    Lane,
    ModelError,
    ModelProtocolError,
    ModelReply,
    ModelUnavailable,
    NightshiftClient,
    NightshiftConfig,
)
from ..adapters.zetaris import CONCEPT_SLUGS, concept_aliases
from ..config import DEFAULT_DEPTH, DEPTHS, model_lanes_mode
from ..models import ModelOpinion
from ..trace import phase
from .common import Candidate, Frame, Measurement, Node, Verdict
from .knowledge import (
    CATEGORY_FAMILIES,
    EVENT_CONCEPTS,
    FILING_EVENTS,
    family,
    money,
    pct,
)

AGENT = "model_lanes"
MIN_SECONDS_FOR_CALL = 5.0  # don't start a model call with less run budget left than this
MAX_RATIONALE_CHARS = 700
MAX_ACTION_CHARS = 900
MAX_NOTE_CHARS = 200
OPINION_LABELS = ("breakdown", "causal", "background")
OPINION_DECISIONS = ("accept", "prune")
KEEP_RULE = "keep-rule"


LANES = ("hypothesiser", "critic", "reporter")
# Investigation depth (ADR-0011) -> the model lanes that run. config.DEPTHS lists the depths.
DEPTH_LANES: dict[str, frozenset[str]] = {
    "quick": frozenset(),
    "thorough": frozenset({"critic"}),
    "deep": frozenset(LANES),
}


@dataclass(frozen=True)
class LaneSwitch:
    enabled: bool
    mode: str  # MODEL_LANES (the operator override): auto, on or off
    reason: str
    lanes: frozenset[str] = frozenset(LANES)  # the lanes on when enabled
    requested: str = "deep"  # the depth asked for
    depth: str = "deep"  # the depth that runs
    requested_by: str = "options.depth"
    degraded: bool = False  # model lanes asked for but not runnable (no Runtime, cap 0)


def requested_depth(
    depth: str | None, model_lanes: str | None, env: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """(depth, where it came from): RunOptions.depth, else the deprecated RunOptions.model_lanes
    (on = deep, off = quick), else MODEL_LANES=on = deep, else quick (DEFAULT_DEPTH)."""
    if depth in DEPTHS:
        return depth, "options.depth"
    if model_lanes == "on":
        return "deep", "options.model_lanes"
    if model_lanes == "off":
        return "quick", "options.model_lanes"
    if model_lanes_mode(env) == "on":
        return "deep", "MODEL_LANES"
    return DEFAULT_DEPTH, "default"


def resolve(
    depth: str | None = None,
    *,
    model_lanes: str | None = None,
    sample: bool,
    config: NightshiftConfig,
    max_calls: int,
    env: Mapping[str, str] | None = None,
) -> LaneSwitch:
    """Which model lanes run (ADR-0011). Sample mode: none, at every depth. MODEL_LANES=off:
    none (operator override). Otherwise the requested depth's lanes (``requested_depth``), or
    none with ``degraded`` when Nightshift is not configured or the call cap is 0."""
    mode = model_lanes_mode(env)
    asked, by = requested_depth(depth, model_lanes, env)
    lanes = DEPTH_LANES[asked]

    def off(reason: str, degraded: bool = False) -> LaneSwitch:
        return LaneSwitch(False, mode, reason, frozenset(), asked, "quick", by, degraded)

    if sample:
        return off(
            f"sample mode: rule lanes only, no model call at any depth ({asked} asked, ADR-0011)"
        )
    if not lanes:
        return off(f"quick ({by}): rule lanes only, no model call")
    if mode == "off":
        return off(f"MODEL_LANES=off: rule lanes only ({asked} asked; operator override)")
    if max_calls <= 0:
        return off(f"MAX_MODEL_CALLS_PER_RUN=0: rule lanes only ({asked} asked)", degraded=True)
    if not config.configured:
        return off(f"Nightshift not configured: rule lanes only ({asked} asked)", degraded=True)
    names = ", ".join(lane for lane in LANES if lane in lanes)
    return LaneSwitch(
        True,
        mode,
        f"{asked} ({by}): Nightshift lanes {names}, cap {max_calls} calls",
        lanes,
        asked,
        asked,
        by,
    )


class ModelLanes:
    """One run's model budget and Nightshift access. ``ask`` never raises."""

    def __init__(
        self,
        *,
        tracer: Any,
        switch: LaneSwitch,
        config: NightshiftConfig | None = None,
        max_calls: int = 12,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        transport: Any = None,  # an httpx transport for tests (MockTransport)
        run_id: str | None = None,
    ) -> None:
        self.tracer = tracer
        self.switch = switch
        self.config = config or NightshiftConfig()
        self.max_calls = max_calls
        self.deadline = deadline
        self.clock = clock
        self.transport = transport
        self.run_id = run_id
        self.calls = 0
        self.tokens = 0
        self.off_reason: str | None = None if switch.enabled else switch.reason
        self.unavailable: str | None = None  # set when Nightshift failed during the run
        self._capped = self._out_of_time = False

    @classmethod
    def off(
        cls, tracer: Any, reason: str = "model lanes off", degraded: bool = False
    ) -> ModelLanes:
        switch = LaneSwitch(
            False, "off", reason, frozenset(), "quick", "quick", "default", degraded
        )
        return cls(tracer=tracer, switch=switch)

    @property
    def enabled(self) -> bool:
        return self.off_reason is None

    def allows(self, lane: str) -> bool:
        """Whether ``lane`` (hypothesiser, critic, reporter) may call the model now."""
        return self.enabled and lane in self.switch.lanes

    @property
    def degraded(self) -> bool:
        return self.switch.degraded or self.unavailable is not None

    def announce(self) -> None:
        s = self.switch
        self.tracer.emit(
            agent=AGENT,
            action="mode",
            status="success",
            lanes="on" if self.enabled else "off",
            lanes_mode=s.mode,
            depth=s.depth,
            depth_requested=s.requested,
            depth_requested_by=s.requested_by,
            model_lanes_on=[lane for lane in LANES if lane in s.lanes] if self.enabled else [],
            max_model_calls=self.max_calls,
            output_summary=s.reason,
        )
        if s.degraded:  # model lanes asked for, none can run: the run goes on the rules
            self.tracer.emit(
                agent=AGENT,
                action="degrade",
                status="degraded",
                depth=s.depth,
                depth_requested=s.requested,
                output_summary=f"{s.reason}; the run continues on the rules",
            )

    def ask(
        self,
        *,
        lane: Lane,
        agent: str,
        action: str,
        system: str,
        user: str,
        node_id: str | None = None,
    ) -> tuple[Any, ModelReply] | None:
        """One JSON call on ``lane``, or None (lanes off, the calling lane not on at this depth,
        cap, budget, unavailable, bad reply)."""
        if not self.enabled or (agent in LANES and agent not in self.switch.lanes):
            return None
        if self.calls >= self.max_calls:
            if not self._capped:
                self._capped = True
                self.tracer.emit(
                    agent=AGENT,
                    action="cap",
                    status="rejected",
                    node_id=node_id,
                    target_agent=agent,
                    output_summary=f"MAX_MODEL_CALLS_PER_RUN={self.max_calls} reached: rule lanes "
                    "only for the rest of the run",
                )
            return None
        timeout = self.config.timeout_seconds
        if self.deadline is not None:
            left = self.deadline - self.clock()
            if left < MIN_SECONDS_FOR_CALL:
                if not self._out_of_time:
                    self._out_of_time = True
                    self.tracer.emit(
                        agent=AGENT,
                        action="budget",
                        status="degraded",
                        node_id=node_id,
                        target_agent=agent,
                        output_summary=f"{left:.0f} s of run budget left: no model call, rules "
                        "only",
                    )
                return None
            timeout = max(1.0, min(timeout, left - 1.0))
        client = NightshiftClient(
            replace(self.config, timeout_seconds=timeout),
            tracer=self.tracer,
            transport=self.transport,
        )
        self.calls += 1
        try:
            with phase(self.tracer, "model_call", owner=agent, lane=lane, node_id=node_id):
                parsed, reply = client.chat_json(
                    [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    lane=lane,
                    temperature=0.0,
                    run_id=self.run_id,
                    agent=agent,
                    action=action,
                    trace_fields={"node_id": node_id, "target_agent": AGENT},
                )
        except ModelUnavailable as exc:
            self.off_reason = self.unavailable = f"model unavailable: {exc.reason}"
            self.tracer.emit(
                agent=agent,
                action="degrade",
                status="degraded",
                node_id=node_id,
                lane=lane,
                target_agent=AGENT,
                output_summary=f"{self.off_reason}; rule lanes for the rest of the run",
            )
            return None
        except ModelProtocolError as exc:
            self.tracer.emit(
                agent=agent,
                action="reject",
                status="rejected",
                node_id=node_id,
                lane=lane,
                output_summary=f"model reply unusable, rules only for this step: {exc}"[:300],
            )
            return None
        except ModelError as exc:  # any other client error: same as a bad reply
            self.tracer.emit(
                agent=agent,
                action="reject",
                status="rejected",
                node_id=node_id,
                lane=lane,
                output_summary=f"model error: {type(exc).__name__}"[:300],
            )
            return None
        total = reply.usage.get("total_tokens")
        if isinstance(total, int):
            self.tokens += total
        return parsed, reply


# --- grounding: no number or name the evidence does not hold ------------------------------------

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A capitalised word not glued to a digit, letter or hyphen before it ("8-K" is a form, not a name).
_NAME = re.compile(r"(?<![\w&-])[A-Z][A-Za-z0-9&]*")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\(\s*)$")


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """Numbers in ``text`` (two digits or more) that do not appear verbatim in ``source``."""
    return [n for n in _NUMBER.findall(text) if len(n) > 1 and n not in source]


def ungrounded_names(text: str, source: str) -> list[str]:
    """Capitalised words in ``text`` (not starting a sentence) that ``source`` does not contain."""
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9&]+", source)}
    out = []
    for match in _NAME.finditer(text):
        if _SENTENCE_START.search(text[: match.start()]):
            continue
        if match.group(0).lower() not in words:
            out.append(match.group(0))
    return out


# --- hypothesiser lane (fast) -------------------------------------------------------------------


def vocabulary(frame: Frame) -> dict[str, list[str]]:
    """What a model proposal may name: concepts the source registers (other than the metric and
    its aliases) and the filing events that apply to this kind of effect and metric family."""
    own = set(concept_aliases(frame.metric)) | {frame.metric, *frame.aliases}
    concepts = list(dict.fromkeys([*CONCEPT_SLUGS, *(ev.concept for ev in EVENT_CONCEPTS)]))
    fam = family(frame.metric)
    events = [fe.key for fe in FILING_EVENTS if frame.kind in fe.modes and fam in fe.families]
    return {"concepts": [c for c in concepts if c not in own], "filing_events": events}


HYPOTHESISER_SYSTEM = (
    "You help a root-cause analysis of a change in a figure reported in SEC filings. Propose "
    "candidate causes to test. You may only name items from the vocabulary given; anything else "
    "is discarded. Every proposal is tested against the filings before it counts. Reply with "
    'one JSON object: {"candidates": [{"type": "concept" | "filing_event", "name": "<from the '
    'vocabulary>", "category": "<one of the categories>", "sign": 1 | -1, "why": "<one short '
    'sentence>"}]}. sign is the effect of one unit of the concept on the figure (-1 for an '
    "expense or charge). Propose at most the number of slots given; an empty list is fine."
)


def _hypothesiser_prompt(
    frame: Frame, parent: Node, taken: list[str], free: int, vocab: dict[str, list[str]]
) -> str:
    events = {fe.key: fe.description for fe in FILING_EVENTS}
    lines = [
        f"Entity: {frame.ticker.upper()}. Figure: {frame.metric} for {frame.period_label}.",
        (
            f"Effect ({frame.kind}): {money(frame.baseline.value)} -> {money(frame.target.value)}, "
            f"change {money(frame.delta)}."
        ),
        f"Explain: {parent.variable} (change {money(parent.effect)}).",
        f"Already being tested: {', '.join(taken) or 'nothing'}.",
        f"Free slots: {free}.",
        "Vocabulary, concepts (US-GAAP): " + ", ".join(vocab["concepts"]) + ".",
        "Vocabulary, filing_events: "
        + "; ".join(f"{k} = {events[k]}" for k in vocab["filing_events"])
        + ".",
        "Categories: " + ", ".join(sorted(CATEGORY_FAMILIES)) + ".",
    ]
    return "\n".join(lines)


def propose(
    lanes: ModelLanes,
    frame: Frame,
    parent: Node,
    *,
    rule_candidates: list[Candidate],
    path_variables: set[str],
    exclude: set[str],
    free: int,
    tracer: Any,
) -> list[Candidate]:
    """Model candidates for the free slots under ``parent``, each validated against the
    vocabulary; every rejected proposal is traced with its reason."""
    if free <= 0 or not lanes.allows("hypothesiser"):
        return []
    vocab = vocabulary(frame)
    taken = [c.variable for c in rule_candidates]
    answer = lanes.ask(
        lane="fast",
        agent="hypothesiser",
        action="model_propose",
        system=HYPOTHESISER_SYSTEM,
        user=_hypothesiser_prompt(frame, parent, taken, free, vocab),
        node_id=parent.node_id,
    )
    if answer is None:
        return []
    parsed, _reply = answer
    items = parsed.get("candidates") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        tracer.emit(
            agent="hypothesiser",
            action="model_reject",
            status="rejected",
            node_id=parent.node_id,
            proposed_by="model",
            output_summary="reply has no candidates list",
        )
        return []
    blocked = set(path_variables) | set(exclude) | set(taken)
    out: list[Candidate] = []
    for item in items:
        cand, why = _validate(item, frame, parent, vocab, blocked, len(rule_candidates) + len(out))
        if cand is not None and len(out) >= free:
            cand, why = None, f"over the {free} free slot(s) (MAX_BRANCHES_PER_LEVEL)"
        if cand is None:
            tracer.emit(
                agent="hypothesiser",
                action="model_reject",
                status="rejected",
                node_id=parent.node_id,
                proposed_by="model",
                input_summary=str(item)[:200],
                output_summary=why,
            )
            continue
        blocked.add(cand.variable)
        out.append(cand)
        tracer.emit(
            agent="hypothesiser",
            action="propose",
            node_id=cand.node_id,
            parent_node_id=cand.parent_id,
            target_agent="analyst",
            input_summary=f"explain {parent.variable} ({money(parent.effect)})",
            output_summary=cand.claim,
            rule=cand.rule,
            depth=cand.depth,
            proposed_by="model",
            model_note=cand.params.get("model_note"),
        )
    return out


def _validate(
    item: Any,
    frame: Frame,
    parent: Node,
    vocab: dict[str, list[str]],
    blocked: set[str],
    index: int,
) -> tuple[Candidate | None, str]:
    if not isinstance(item, dict):
        return None, "not an object"
    kind, name = item.get("type"), item.get("name")
    note = item.get("why") if isinstance(item.get("why"), str) else ""
    note = " ".join(note.split())[:MAX_NOTE_CHARS]
    common = {
        "node_id": f"{parent.node_id}.{index + 1}",
        "parent_id": parent.node_id,
        "depth": parent.depth + 1,
        "proposed_by": "model",
    }
    if kind == "concept":
        if name not in vocab["concepts"]:
            return None, f"concept {name!r} is not in the vocabulary the source can query"
        if name in blocked:
            return None, f"{name} is already on the path, tested or proposed (no cycles)"
        known = next((ev for ev in EVENT_CONCEPTS if ev.concept == name), None)
        if known is not None:  # knowledge wins over the model on sign and category
            sign, category, description = known.sign, known.category, known.description
        else:
            sign, category = item.get("sign"), item.get("category")
            if sign not in (1, -1) or isinstance(sign, bool):
                return None, f"sign {sign!r} is not 1 or -1"
            if category not in CATEGORY_FAMILIES:
                return None, f"category {category!r} is not one of {sorted(CATEGORY_FAMILIES)}"
            description = name
        return (
            Candidate(
                **common,
                rule="event_concept",
                variable=name,
                claim=f"An abnormal change in {name} drives the change in {parent.variable} "
                "(model hypothesis)",
                category=category,
                params={
                    "concept": name,
                    "sign": sign,
                    "description": description,
                    "model_note": note,
                },
            ),
            "",
        )
    if kind == "filing_event":
        if name not in vocab["filing_events"]:
            return None, f"filing event {name!r} does not apply or is not in the vocabulary"
        fe = next(fe for fe in FILING_EVENTS if fe.key == name)
        variable = f"8-K {'/'.join(fe.items)}"
        if variable in blocked:
            return None, f"{variable} is already on the path, tested or proposed (no cycles)"
        claim = (
            f"A {fe.description} between the two reports changed {frame.metric}"
            if frame.kind == "restatement"
            else f"A {fe.description} during the period changed {parent.variable}"
        )
        return (
            Candidate(
                **common,
                rule="filing_event",
                variable=variable,
                claim=claim + " (model hypothesis)",
                category=fe.category,
                params={"event": fe.key, "model_note": note},
            ),
            "",
        )
    return None, f"type {kind!r} is not concept or filing_event"


# --- critic lane (deep) -------------------------------------------------------------------------

CRITIC_SYSTEM = (
    "You review one link of a root-cause analysis of a figure reported in SEC filings. Judge it "
    "only from the measurement given. Labels: breakdown (locates the change inside an "
    "accounting identity, not a cause), causal (an event or abnormal change whose removal moves "
    "the figure back), background (a normal condition, not a cause). Decision: accept or prune. "
    'Reply with one JSON object: {"label": "breakdown" | "causal" | "background", "decision": '
    '"accept" | "prune", "rationale": "<one paragraph; quote numbers only as they appear in the '
    'measurement>"}.'
)


def _measurement_facts(cand: Candidate, m: Measurement, frame: Frame) -> str:
    root = frame.delta
    raw = None if m.effect is None or root == 0 else m.effect / root
    lines = [
        (
            f"Figure: {frame.metric} of {frame.ticker.upper()} for {frame.period_label} "
            f"({frame.kind}): {money(frame.baseline.value)} -> {money(frame.target.value)}, "
            f"effect {money(root)}."
        ),
        f"Hypothesis ({cand.rule}): {cand.claim}.",
        f"Tested variable: {cand.variable}.",
        f"Measured contribution: {money(m.effect)} ({pct(raw)} of the effect).",
        f"Counterfactual (variable reset to baseline): {money(m.counterfactual)}.",
        f"Variable baseline {money(m.variable_baseline)}, target {money(m.variable_target)}.",
        f"Filing events in the window: {len(m.events)}; outside it: {m.events_outside_window}.",
    ]
    if m.mechanism:
        lines.append("Mechanism: " + "; ".join(m.mechanism) + ".")
    if m.missing:
        lines.append(f"Missing from the registered sources: {m.missing}.")
    if m.error:
        lines.append(f"Not testable: {m.error}.")
    return "\n".join(lines)


def second_opinion(
    lanes: ModelLanes, cand: Candidate, m: Measurement, v: Verdict, frame: Frame, tracer: Any
) -> ModelOpinion | None:
    """The deep lane's independent judgement of one link (it is not shown the rule verdict)."""
    if not lanes.allows("critic"):
        return None
    facts = _measurement_facts(cand, m, frame)
    answer = lanes.ask(
        lane="deep",
        agent="critic",
        action="model_review",
        system=CRITIC_SYSTEM,
        user=facts,
        node_id=cand.node_id,
    )
    if answer is None:
        return None
    parsed, reply = answer
    label = parsed.get("label") if isinstance(parsed, dict) else None
    decision = parsed.get("decision") if isinstance(parsed, dict) else None
    if label not in OPINION_LABELS or decision not in OPINION_DECISIONS:
        tracer.emit(
            agent="critic",
            action="model_reject",
            status="rejected",
            node_id=cand.node_id,
            output_summary=f"second opinion not usable (label {label!r}, decision {decision!r})",
        )
        return None
    rationale = parsed.get("rationale")
    rationale = (
        " ".join(rationale.split())[:MAX_RATIONALE_CHARS] if isinstance(rationale, str) else ""
    )
    stray = ungrounded_numbers(rationale, facts)
    grounded = bool(rationale) and not stray
    rule_label = "causal" if v.label == "leverage" else v.label
    rule_decision = v.decision
    if rule_decision == "accept":
        agrees = decision == "accept" and label == rule_label
    elif rule_decision == "prune":
        agrees = decision == "prune"
    else:  # frontier: plausible, not measurable yet; only a prune is a disagreement
        agrees = decision == "accept"
    opinion = ModelOpinion(
        lane=reply.lane,
        requested_model=reply.requested_model,
        routed_model=reply.routed_model,
        provider=reply.provider,
        receipt_id=reply.receipt_id,
        decision=decision,
        label=label,
        rationale=rationale if grounded else None,
        grounded=grounded,
        agrees=agrees,
        rule_decision=rule_decision,
    )
    tracer.emit(
        agent="critic",
        action="second_opinion",
        status="success" if agrees else "rejected",
        node_id=cand.node_id,
        target_agent="gate" if not agrees else "orchestrator",
        label=label,
        lane="deep",
        routed_model=reply.routed_model,
        output_summary=f"model: {decision} ({label}); rules: {rule_decision} ({v.label}); "
        + ("agrees" if agrees else "disagrees")
        + ("" if grounded else f"; rationale dropped (numbers not in the measurement: {stray})"),
    )
    return opinion


def dissent_request(v: Verdict, opinion: ModelOpinion) -> dict[str, Any]:
    """critic_escalation gate request for a disagreement. The proposal keeps the rule verdict;
    a human (interactive or scripted) may only prune, never accept what the rules did not."""
    options = (KEEP_RULE,) if v.decision == "prune" else (KEEP_RULE, "prune")
    return {
        "question": f"Rule critic: {v.decision} ({v.label}); model second opinion: "
        f"{opinion.decision} ({opinion.label}). Keep the rule verdict"
        + (", or prune?" if len(options) > 1 else "?"),
        "options": options,
        "proposal": KEEP_RULE,
    }


# --- reporter lane (fast) -----------------------------------------------------------------------

REPORTER_SYSTEM = (
    "You rewrite a recommended action for a financial analyst in plain language, in at most "
    "three sentences. Keep its meaning. Use only the numbers, dates, names and filing references "
    "it contains, written exactly as given; add nothing else. Reply with one JSON object: "
    '{"action": "<the rewritten action>"}.'
)


def draft_action(lanes: ModelLanes, template: str, context: str, tracer: Any) -> str | None:
    """The fast lane's wording of ``template``, or None (the template stays)."""
    if not lanes.allows("reporter") or not template:
        return None
    answer = lanes.ask(
        lane="fast",
        agent="reporter",
        action="model_wording",
        system=REPORTER_SYSTEM,
        user=f"Action: {template}\nContext: {context}",
    )
    if answer is None:
        return None
    parsed, _reply = answer
    text = parsed.get("action") if isinstance(parsed, dict) else None
    why = None
    if not isinstance(text, str) or not text.strip():
        why = "no action text"
    else:
        text = " ".join(text.split())
        source = f"{template} {context}"
        numbers, names = ungrounded_numbers(text, source), ungrounded_names(text, source)
        if len(text) > MAX_ACTION_CHARS:
            why = f"longer than {MAX_ACTION_CHARS} characters"
        elif numbers or names:
            why = f"adds numbers {numbers} or names {names} absent from the template"
    if why:
        tracer.emit(
            agent="reporter",
            action="model_reject",
            status="rejected",
            output_summary=f"model wording not used, template kept: {why}"[:300],
        )
        return None
    tracer.emit(
        agent="reporter",
        action="reword",
        status="success",
        output_summary=text[:300],
        input_summary=template[:200],
    )
    return text
