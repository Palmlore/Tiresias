"""Orchestrator: Loop A drills down in the registered sources; Loop B onboards a missing one
(docs/architecture.md).

Loop A: framer -> (hypothesiser -> analyst <-> critic [-> gate])* -> gap analyst? -> reporter
Loop B: gap analyst's need -> source scout -> onboarder [source gate] -> analyst + critic
        (reconcile) -> stopping rule -> reporter

Each level: the hypothesiser proposes up to MAX_BRANCHES_PER_LEVEL candidates for the current
node; the analyst measures each; the critic accepts, prunes (back to the hypothesiser),
sends it back to the analyst with a corrected scope (bounded by MAX_RETRIES_PER_NODE), or
marks it as a frontier. Then the run stops at the first accepted causal link the asker can act
on (causal-criteria.md §5), or digs under the strongest accepted breakdown, up to
MAX_CAUSAL_DEPTH levels and RUN_BUDGET_SECONDS. Every run ends in exactly one end state.

``run_investigation`` runs Loop A, then Loop B when Loop A ends at the evidence frontier with an
information need; ``run_loop_a`` runs Loop A only. Both take the same arguments and never raise.

Memory (``run_investigation`` only, agents/memory.py, ADR-0005): in warm mode the framed effect is
the recall query, recalled lessons make the hypothesiser test their steps first and the critic
judges each lesson; a successful run stores its lesson behind the memory gate. Cold: no memory.

CLI: python -m tiresias_agent.orchestrator <request.json> [--live] [--loop-a] [--logs-dir DIR] [--depth D] [--memory warm|cold]
runs run_investigation (Loop A only with --loop-a) and prints the RunResult JSON on stdout (trace lines go to stderr and logs/trace-<run_id>.jsonl).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .adapters.catalog import CatalogError, Discovery, make_discovery
from .adapters.cognition import CognitionClient, CognitionConfig
from .adapters.nightshift import NightshiftConfig
from .adapters.zetaris import (
    DEFAULT_FORMS,
    FACTS_CACHE,
    Entity,
    EvidenceSource,
    ZetarisClient,
    ZetarisConfig,
    check_liveness,
    facts_table,
    make_source,
    object_ticker,
)
from .agents import (
    analyst,
    critic,
    entity_resolver,
    gap_analyst,
    hypothesiser,
    memory,
    model_lanes,
    onboarder,
    reconciler,
    reporter,
    scope,
    source_scout,
)
from .agents.common import MOST_OF_EFFECT, Candidate, Frame, LoopError, Measurement, Node
from .agents.framer import FrameFailure
from .agents.framer import frame as frame_question
from .agents.framer import summary as frame_summary
from .agents.knowledge import (
    BREAKDOWN_RELATION,
    EVENT_RULES,
    actionable_family,
    money,
    residual_text,
    ruled_out_text,
)
from .config import DEPTHS, LoopBudgets, expected_seconds, max_over_explained, settings
from .gates import Answer, Gate, GateRequest
from .models import Entity as EntityRef
from .models import (
    ErrorDetail,
    ErrorResponse,
    ExpectedWait,
    InformationNeed,
    Period,
    RunDepth,
    RunRequest,
    RunResult,
    RunStats,
    ScopeCheck,
)
from .trace import Tracer, new_run_id, phase

AGENT = "orchestrator"
REPO_ROOT = Path(__file__).resolve().parents[4]
# An interactive gate channel (gates.py): runs.py's GateChannel.ask, or the CLI's stdin prompt.
Ask = Callable[[GateRequest], "str | Answer | None"]


class CountingSource:
    """Counts queries for RunStats; otherwise transparent."""

    def __init__(self, inner: EvidenceSource) -> None:
        self.inner = inner
        self.name = inner.name
        self.queries = 0

    def entities(self):
        self.queries += 1
        return self.inner.entities()

    def concept_series(self, entity: str, concept: str):
        self.queries += 1
        return self.inner.concept_series(entity, concept)

    def filings(self, entity: str, forms: Sequence[str] = DEFAULT_FORMS):
        self.queries += 1
        return self.inner.filings(entity, forms)

    def refresh(self) -> None:
        refresh = getattr(self.inner, "refresh", None)
        if callable(refresh):
            refresh()


class _Budget(Exception):
    pass


class LoopA:
    def __init__(
        self,
        request: RunRequest,
        *,
        source: EvidenceSource,
        tracer: Any,
        gate: Gate,
        budgets: LoopBudgets,
        clock: Callable[[], float] = time.monotonic,
        lanes: model_lanes.ModelLanes | None = None,
        memory_run: memory.MemoryRun | None = None,
    ) -> None:
        self.request = request
        self.memory = memory_run
        self.source = CountingSource(source)
        self.tracer = tracer
        self.gate = gate
        self.budgets = budgets
        self.lanes = lanes or model_lanes.ModelLanes.off(tracer)
        self.clock = clock
        self.started = clock()
        self.nodes: list[Node] = []
        self.hops = self.retries = self.prunes = self.gate_decisions = 0
        # Evidence from before Loop A (entity resolution and registration), shown on the root.
        self.prelude_evidence: list = []

    # --- budget -------------------------------------------------------------------------------

    def _check_time(self) -> None:
        if self.clock() - self.started > self.budgets.run_seconds:
            raise _Budget(f"wall-clock budget of {self.budgets.run_seconds:.0f} s reached")

    # --- one candidate: analyst <-> critic (<-> gate) ------------------------------------------

    def _test(self, frame: Frame, cand: Candidate) -> Node:
        attempt, scope = 0, analyst.first_scope(cand)
        evidence = []
        while True:
            self._check_time()
            m = analyst.measure(
                cand, frame, self.source, attempt=attempt, scope=scope, tracer=self.tracer
            )
            self.hops += 1
            evidence.extend(m.evidence)
            v = critic.judge(
                cand, m, frame, retries_left=self.budgets.max_retries_per_node - attempt
            )
            if v.escalation:
                self.tracer.emit(
                    agent=critic.AGENT,
                    action="escalate",
                    node_id=cand.node_id,
                    target_agent="gate",
                    label=v.label,
                    share_explained=v.share,
                    output_summary=v.escalation["question"],
                )
                decision = self.gate.decide(
                    GateRequest(
                        gate="critic_escalation",
                        question=v.escalation["question"],
                        options=tuple(v.escalation["options"]),
                        proposal=v.escalation["proposal"],
                        node_id=cand.node_id,
                        requested_by=critic.AGENT,
                        context={
                            "share": v.raw_share,
                            "label": v.label,
                            "variable": cand.variable,
                            "claim": cand.claim,
                        },
                    )
                )
                self.gate_decisions += 1
                v = critic.apply_gate(v, decision.choice)
            if v.decision == "retry":
                critic.trace_verdict(self.tracer, cand, m, v)
                attempt += 1
                self.retries += 1
                scope = v.retry_scope or scope
                continue
            break
        opinion = None
        if self.lanes.allows("critic"):  # deep lane: advisory second opinion, rules decide
            opinion = model_lanes.second_opinion(self.lanes, cand, m, v, frame, self.tracer)
            if opinion is not None and not opinion.agrees:
                ask = model_lanes.dissent_request(v, opinion)
                decision = self.gate.decide(
                    GateRequest(
                        gate="critic_escalation",
                        question=ask["question"],
                        options=tuple(ask["options"]),
                        proposal=ask["proposal"],
                        node_id=cand.node_id,
                        requested_by=critic.AGENT,
                        context={
                            "rule": {"decision": v.decision, "label": v.label},
                            "model": {"decision": opinion.decision, "label": opinion.label},
                        },
                    )
                )
                self.gate_decisions += 1
                opinion = opinion.model_copy(update={"gate_choice": decision.choice})
                if decision.choice == "prune":
                    v = critic.apply_gate(v, "prune")
        critic.trace_verdict(self.tracer, cand, m, v)
        if v.decision == "prune":
            self.prunes += 1
        return Node(
            node_id=cand.node_id,
            parent_id=cand.parent_id,
            depth=cand.depth,
            variable=cand.variable,
            claim=cand.claim,
            label=v.label,
            verdict={"accept": "accepted", "prune": "pruned", "frontier": "frontier"}[v.decision],
            rule=cand.rule,
            category=cand.category,
            evidence_status=v.evidence_status,
            share=v.share,
            raw_share=v.raw_share,
            effect=m.effect,
            reason=v.reason,
            attempts=attempt + 1,
            evidence=evidence,
            measurement=m,
            proposed_by=cand.proposed_by,
            model_opinion=opinion,
        )

    # --- the loop -----------------------------------------------------------------------------

    def run(self, frame: Frame) -> tuple[str, list[Node], InformationNeed | None, str | None]:
        """Returns (end_state, chosen nodes, information need, note)."""
        root = Node(
            node_id="n0",
            parent_id=None,
            depth=0,
            variable=frame.metric,
            claim=frame_summary(frame),
            label="breakdown",
            verdict="root",
            evidence_status="evidenced",
            share=1.0,
            raw_share=1.0,
            effect=frame.delta,
            evidence=[*self.prelude_evidence, frame.evidence],
        )
        self.nodes.append(root)
        parent, path, tested = root, {root.variable}, set()
        frontier: list[Node] = []
        asker = frame.asker
        try:
            for _level in range(self.budgets.max_causal_depth):
                self._check_time()
                cands = hypothesiser.propose(
                    frame,
                    parent,
                    path,
                    max_branches=self.budgets.max_branches_per_level,
                    tracer=self.tracer,
                    exclude=tested,
                    prefer=self.memory.preferences() if self.memory else None,
                )
                free = self.budgets.max_branches_per_level - len(cands)
                if free > 0 and self.lanes.allows("hypothesiser"):  # fast lane: free slots only
                    cands += model_lanes.propose(
                        self.lanes,
                        frame,
                        parent,
                        rule_candidates=cands,
                        path_variables=path,
                        exclude=tested,
                        free=free,
                        tracer=self.tracer,
                    )
                if not cands:
                    return self._no_more_rules(frame, parent, frontier)
                level_nodes = []
                for i, cand in enumerate(cands):
                    node = self._test(frame, cand)
                    tested.add(cand.variable)
                    level_nodes.append(node)
                    # A recalled lesson's step held as recorded: the rest of the level waits
                    # (memory.py; run_investigation re-runs Loop A in full if no cause follows).
                    if self.memory and self.memory.observe(node, frame) and cands[i + 1 :]:
                        self.memory.skip(cands[i + 1 :], node)
                        break
                level_nodes.sort(key=_rules_order)  # the why-tree keeps the rules' order
                self.nodes.extend(level_nodes)
                frontier += [n for n in level_nodes if n.verdict == "frontier"]
                accepted = [n for n in level_nodes if n.verdict == "accepted"]
                causal = [n for n in accepted if n.label == "causal"]
                breakdowns = [n for n in accepted if n.label == "breakdown"]
                if causal:
                    causal.sort(key=lambda n: n.raw_share or 0, reverse=True)
                    strong = [n for n in causal if (n.raw_share or 0) >= MOST_OF_EFFECT]
                    if len(causal) >= 2 and not strong:
                        return self._stop("branched", causal, None)
                    main = causal[0]
                    if actionable_family(main.category or "", list(asker.levers), asker.role):
                        return self._stop("actionable", [main], None)
                    # Not in the asker's levers: ask why once more, under this cause.
                    parent = main
                elif breakdowns:
                    parent = max(breakdowns, key=lambda n: n.raw_share or 0)
                elif frontier:
                    return self._frontier(frame, frontier)
                else:
                    return self._no_more_rules(frame, parent, frontier)
                path.add(parent.variable)
                self.tracer.emit(
                    agent=AGENT,
                    action="expand",
                    node_id=parent.node_id,
                    target_agent="hypothesiser",
                    label=parent.label,
                    output_summary=f"dig under {parent.variable} (level {parent.depth + 1})",
                )
            return self._stop(
                "budget", [], None, note=f"MAX_CAUSAL_DEPTH={self.budgets.max_causal_depth} reached"
            )
        except _Budget as err:
            return self._stop("budget", [], None, note=str(err))

    def _no_more_rules(self, frame: Frame, parent: Node, frontier: list[Node]):
        if parent.label == "causal" and parent.verdict == "accepted":
            return self._stop("external-driver", [parent], None)
        if frontier:
            return self._frontier(frame, frontier)
        tested = self.nodes[1:]
        if (
            parent.verdict == "root"
            and tested
            and all(n.verdict == "pruned" and n.rule in EVENT_RULES for n in tested)
        ):
            # Every candidate was an accounting/filing event and each was ruled out: the
            # filed totals have nothing more to say; ask for a breakdown, not the same total.
            ruled_out = list(dict.fromkeys(ruled_out_text(n.rule, n.variable) for n in tested))
            need = gap_analyst.for_breakdown(frame, ruled_out, self.tracer)
            return self._stop("evidence-frontier", [], need)
        need = gap_analyst.for_variable(
            frame,
            variable=parent.variable,
            node_id=parent.node_id,
            reason=f"no rule or registered source explains {parent.variable} further",
            tracer=self.tracer,
            ticker=frame.ticker,
            cik=frame.cik,
            period=Period(start=frame.period_start, end=frame.period_end),
        )
        return self._stop("evidence-frontier", [], need)

    def _frontier(self, frame: Frame, frontier: list[Node]):
        best = frontier[0]
        need = gap_analyst.for_candidate(frame, best, self.tracer)
        return self._stop("evidence-frontier", [best], need)

    def _stop(self, end_state: str, chosen: list[Node], need, note: str | None = None):
        self.tracer.emit(
            agent=AGENT,
            action="stop",
            status="success",
            node_id=chosen[0].node_id if chosen else None,
            target_agent="reporter",
            end_state=end_state,
            output_summary=note or end_state,
        )
        return end_state, chosen, need, note

    def stats(self) -> RunStats:
        return RunStats(
            hops=self.hops,
            queries=self.source.queries,
            seconds=round(self.clock() - self.started, 3),
            retries=self.retries,
            prunes=self.prunes,
            gate_decisions=self.gate_decisions,
        )


def _rules_order(node: Node) -> tuple[int, str]:
    tail = node.node_id.rsplit(".", 1)[-1]
    return (int(tail) if tail.isdigit() else 10**6, node.node_id)


@dataclass
class _Run:
    """One run's shared state: Loop A's outcome, which Loop B may continue, then the report."""

    request: RunRequest
    tracer: Any
    budgets: LoopBudgets
    sample: bool
    common: dict[str, Any]
    clock: Callable[[], float]
    gate: Gate | None = None
    loop: LoopA | None = None
    frame: Frame | None = None
    end_state: str | None = None
    chosen: list[Node] = field(default_factory=list)
    need: InformationNeed | None = None
    note: str | None = None
    error: ErrorDetail | None = None
    sources_onboarded: int = 0
    started: float = 0.0
    onboarded: list[Entity] = field(default_factory=list)  # sources this run registered
    prelude_evidence: list = field(default_factory=list)
    prelude_gate_decisions: int = 0
    discovery: Discovery | None = None
    scope: ScopeCheck | None = None  # set when the scope check stopped the run
    lanes: model_lanes.ModelLanes | None = None
    pinned: list[str] = field(default_factory=list)  # facts tables this run keeps cached
    memory: memory.MemoryRun | None = None  # H-MEM (run_investigation only)


def _check_zetaris(run: _Run, source: EvidenceSource) -> None:
    """Live: the liveness probe (adapters.zetaris.LIVENESS, cached for its TTL), traced as one
    ``liveness`` line. If Zetaris is down the run's client sends nothing more, so every read and
    registration degrades to the sample at once instead of waiting on timeouts. Never raises."""
    client = _find(source, "client")
    if not isinstance(client, ZetarisClient):
        return
    try:
        check_liveness(client, tracer=run.tracer)
    except Exception as err:  # noqa: BLE001 - the probe is advisory
        run.tracer.emit(
            agent=AGENT,
            action="liveness",
            status="error",
            outcome="inconclusive",
            output_summary=f"liveness probe failed: {type(err).__name__}",
        )


def _pin(run: _Run, ticker: str | None) -> None:
    """Live: keep this entity's companyfacts table cached while the run goes on (it is never the
    least recently used table evicted under ZETARIS_MAX_CACHED_FACTS)."""
    if run.sample or not ticker:
        return
    try:
        table = facts_table(object_ticker(ticker))
    except ValueError:
        return
    if table not in run.pinned:
        FACTS_CACHE.pin(table)
        run.pinned.append(table)


def _release_discovery(run: _Run, discovery: Any, keep: Sequence[str | None]) -> None:
    """Live: uncache the discovery tables this run cached (candidate submissions tables that were
    not chosen, full-text search tables), except ``keep``'s. Never raises (BACKLOG B-24)."""
    release = getattr(discovery, "release_cache", None)
    if run.sample or not callable(release):
        return
    try:
        release([k for k in keep if k])
    except Exception as err:  # noqa: BLE001 - releasing a cache never fails a run
        run.tracer.emit(
            agent=AGENT,
            action="uncache",
            status="error",
            output_summary=f"discovery cache release failed: {type(err).__name__}",
        )


def _keep(run: _Run, *extra: str | None) -> list[str | None]:
    asked = run.request.entity.ticker if run.request.entity else None
    return [asked, *(e.ticker for e in run.onboarded), *extra]


def _finish_live(run: _Run) -> None:
    """End of a run: release the discovery tables it still holds and unpin its tables."""
    try:
        _release_discovery(run, run.discovery, _keep(run))
    finally:
        for table in run.pinned:
            FACTS_CACHE.unpin(table)
        run.pinned.clear()


def _fail(run: _Run, err: Exception) -> None:
    if isinstance(err, LoopError):
        detail = ErrorDetail(type=err.error_type, message=str(err), recoverable=err.recoverable)
    else:
        detail = ErrorDetail(type=type(err).__name__, message=str(err)[:500], recoverable=False)
    run.tracer.emit(
        agent=AGENT,
        action="error",
        status="error",
        error_type=detail.type,
        output_summary=detail.message,
    )
    run.error = detail


def _start(
    request: RunRequest,
    *,
    source: EvidenceSource | None,
    tracer: Tracer | None,
    gate: Gate | None,
    budgets: LoopBudgets | None,
    run_id: str | None,
    parent_run_id: str | None,
    logs_dir: str | Path | None,
    clock: Callable[[], float],
    lanes: model_lanes.ModelLanes | None = None,
    ask: Ask | None = None,
) -> tuple[_Run, EvidenceSource | None]:
    sample = request.options.sample_mode
    sample = settings.sample_mode if sample is None else sample
    run_id = run_id or (tracer.run_id if tracer and tracer.run_id else new_run_id())
    if tracer is None:
        tracer = Tracer(
            run_id,
            parent_run_id,
            logs_dir=logs_dir,
            defaults={"mode": "sample" if sample else "live"},
        )
    budgets = budgets or LoopBudgets.from_env()
    trace_file = str(tracer.trace_file) if tracer.trace_file else None
    tracer.emit(
        agent=AGENT,
        action="start",
        input_summary=request.question[:200],
        output_summary=f"budgets depth={budgets.max_causal_depth} "
        f"branches={budgets.max_branches_per_level} retries={budgets.max_retries_per_node} "
        f"seconds={budgets.run_seconds:.0f} sources={budgets.max_new_sources}",
    )
    common = {
        "run_id": run_id,
        "trace_file": trace_file,
        "sample_mode": sample,
        "parent_run_id": parent_run_id,
        "tracer": tracer,
    }
    run = _Run(request, tracer, budgets, sample, common, clock, started=clock())
    run.lanes = lanes or _lanes(request, run)
    run.lanes.announce()
    try:
        run.gate = gate or Gate(
            request.options.hitl_mode or settings.hitl_mode,
            tracer=tracer,
            scripted=request.options.decisions,
            ask=ask,
        )
        source = source or make_source(sample, tracer)
    except Exception as err:  # noqa: BLE001 - a run must end in a structured result
        _fail(run, err)
    if run.error is None and source is not None and not sample:
        _check_zetaris(run, source)
    return run, source


def _switch(
    request: RunRequest, sample: bool, budgets: LoopBudgets
) -> tuple[model_lanes.LaneSwitch, NightshiftConfig | None]:
    """The run's depth and model lanes (ADR-0010, ADR-0011) from RunOptions.depth (or the
    deprecated RunOptions.model_lanes), MODEL_LANES, sample mode and the Runtime configuration.
    Never raises: a bad configuration means quick (rules only), degraded."""
    opts = request.options
    try:
        config = NightshiftConfig.from_env()
        switch = model_lanes.resolve(
            opts.depth,
            model_lanes=opts.model_lanes,
            sample=sample,
            config=config,
            max_calls=budgets.max_model_calls,
        )
    except Exception as err:  # noqa: BLE001 - the model is optional
        asked, by = model_lanes.requested_depth(opts.depth, opts.model_lanes)
        reason = f"model lanes off: {type(err).__name__}: {err}"[:300]
        degraded = bool(model_lanes.DEPTH_LANES[asked]) and not sample
        switch = model_lanes.LaneSwitch(
            False, "auto", reason, frozenset(), asked, "quick", by, degraded
        )
        return switch, None
    return switch, config


def depth_plan(
    request: RunRequest,
    switch: model_lanes.LaneSwitch | None = None,
    *,
    unavailable: str | None = None,
) -> RunDepth:
    """The depth a request runs at and its expected wait, before the run (the POST /runs 202
    body) or after it (RunResult.depth). The tier is not known before entity resolution, so
    the wait spans warm and cold registration (config.expected_seconds)."""
    sample = request.options.sample_mode
    sample = settings.sample_mode if sample is None else sample
    if switch is None:
        switch, _ = _switch(request, sample, LoopBudgets.from_env())
    lo, hi, basis = expected_seconds(None, switch.depth, sample)
    lanes = [lane for lane in model_lanes.LANES if lane in switch.lanes] if switch.enabled else []
    note = switch.reason
    if unavailable:
        note = f"{note}; then {unavailable}: rule lanes for the rest of the run"
    return RunDepth(
        requested=switch.requested,
        requested_by=switch.requested_by,
        effective=switch.depth,
        lanes=lanes,
        degraded=switch.degraded or unavailable is not None,
        note=note,
        expected_wait=ExpectedWait(min_seconds=lo, max_seconds=hi, basis=basis),
    )


def _lanes(request: RunRequest, run: _Run) -> model_lanes.ModelLanes:
    """The run's model lanes (see ``_switch``)."""
    switch, config = _switch(request, run.sample, run.budgets)
    if config is None:
        return model_lanes.ModelLanes(tracer=run.tracer, switch=switch)
    return model_lanes.ModelLanes(
        tracer=run.tracer,
        switch=switch,
        config=config,
        max_calls=run.budgets.max_model_calls,
        deadline=run.started + run.budgets.run_seconds,
        clock=run.clock,
        run_id=run.common["run_id"],
    )


def _unframed(run: _Run, reason: str) -> None:
    """The effect cannot be framed (entity or metric not in the sources): evidence frontier
    for the metric itself, with the reason in the information need."""
    request = run.request
    run.end_state = "evidence-frontier"
    run.need = gap_analyst.for_variable(
        None,
        variable=request.metric,
        node_id="n0",
        reason=reason,
        tracer=run.tracer,
        ticker=(request.entity.ticker or request.entity.name or "").lower(),
        cik=request.entity.cik,
        period=request.period,
    )


def _prepare_entity(run: _Run, source: EvidenceSource) -> bool:
    """Resolve the asked entity and, in live mode, register it if Zetaris does not have it yet
    (behind the source gate, counted in MAX_NEW_SOURCES_PER_RUN, bounded by the run budget).
    Returns False when the run must stop at the evidence frontier; never raises."""
    tracer = run.tracer
    client = _find(source, "client")

    def discovery() -> Discovery:
        if run.discovery is None:
            run.discovery = make_discovery(
                run.sample, tracer, data_dir=_find(source, "data_dir"), client=client
            )
        return run.discovery

    with phase(tracer, "resolve_entity", owner=entity_resolver.AGENT) as done:
        res = entity_resolver.resolve(
            run.request,
            source=source,
            sample=run.sample,
            tracer=tracer,
            discovery=discovery,
            views=_find(source, "registered_views"),
        )
        done.update(outcome="resolved" if res.ok else res.error_type, registered=res.registered)
    if res.error_type == "InvalidInput":
        raise LoopError("InvalidInput", res.reason)
    with phase(tracer, "scope_check", owner=scope.AGENT) as done:
        verdict = scope.check(
            run.request,
            res,
            sample=run.sample,
            source=source,
            discovery=discovery,
            tracer=tracer,
        )
        done.update(outcome="in_scope" if verdict.in_scope else "out_of_scope")
    if not verdict.in_scope:
        # Stop before Loop A: a plain reason and runnable alternatives (end state
        # evidence-frontier, RunResult.scope). An unresolved entity keeps its information need.
        run.scope = verdict
        if res.ok:
            run.end_state = "evidence-frontier"
        else:
            _unframed(run, res.reason)
        return False
    if not res.ok or res.entity is None:
        _unframed(run, res.reason)
        return False
    entity = res.entity
    _pin(run, entity.ticker)
    if not res.registered:
        assert run.gate is not None
        with phase(tracer, "register_entity", owner=onboarder.AGENT) as done:
            reg = onboarder.onboard_entity(
                entity,
                metric=run.request.metric,
                gate=run.gate,
                used=len(run.onboarded),
                cap=run.budgets.max_new_sources,
                tracer=tracer,
                client=client,
                deadline=run.started + run.budgets.run_seconds,
                clock=run.clock,
            )
            done.update(outcome=reg.status)
        run.prelude_gate_decisions += int(reg.gate_decided)
        if not reg.ok:
            _unframed(run, f"{res.reason}; onboarder: {reg.reason}")
            return False
        run.onboarded.append(entity)
        run.sources_onboarded = len(run.onboarded)
        refresh = getattr(source, "refresh", None)
        if callable(refresh):
            refresh()
        run.prelude_evidence = [*res.evidence, *reg.evidence]
    run.request = run.request.model_copy(
        update={"entity": EntityRef(ticker=entity.ticker, cik=entity.cik, name=entity.name or None)}
    )
    return True


def _loop_a(run: _Run, source: EvidenceSource, *, again: bool = False) -> None:
    """Entity check, framer + Loop A. Records the outcome on ``run``; never raises.

    ``again``: the memory fallback, Loop A once more from the frame (entity checks done, no new
    recall), carrying the first pass's counters so the stats show the full cost."""
    tracer = run.tracer
    try:
        if not again and not _prepare_entity(run, source):
            return
        request = run.request
        assert run.gate is not None
        with phase(tracer, "loop_a", owner=AGENT) as done:
            prior = run.loop
            run.loop = LoopA(
                request,
                source=source,
                tracer=tracer,
                gate=run.gate,
                budgets=run.budgets,
                clock=run.clock,
                lanes=run.lanes,
                memory_run=run.memory,
            )
            run.loop.started = run.started  # one budget for the run, registration included
            run.loop.gate_decisions += run.prelude_gate_decisions
            run.loop.prelude_evidence = list(run.prelude_evidence)
            if prior is not None:
                for name in ("hops", "retries", "prunes", "gate_decisions"):
                    setattr(run.loop, name, getattr(prior, name))
                run.loop.source.queries = prior.source.queries
            try:
                with phase(tracer, "frame", owner="framer"):
                    run.frame = frame_question(request, run.loop.source, tracer)
            except FrameFailure as err:
                tracer.emit(
                    agent="framer",
                    action="frame",
                    status="rejected",
                    node_id="n0",
                    target_agent="gap_analyst",
                    output_summary=str(err),
                )
                _unframed(run, str(err))
                done.update(end_state=run.end_state)
                return
            if run.memory is not None and not again and run.memory.mode == "warm":
                with phase(tracer, "recall", owner=memory.AGENT) as recalled:
                    run.memory.recall(run.frame)
                    recalled.update(
                        outcome="recalled" if run.memory.available else "unavailable",
                        memory_ids=[lsn.memory_id for lsn in run.memory.lessons],
                    )
            run.end_state, run.chosen, run.need, run.note = run.loop.run(run.frame)
            done.update(end_state=run.end_state)
    except Exception as err:  # noqa: BLE001 - LoopError or a bug: a structured result either way
        _fail(run, err)


def _report(run: _Run) -> RunResult:
    loop = run.loop
    stats = loop.stats() if loop else RunStats(gate_decisions=run.prelude_gate_decisions)
    stats.sources_onboarded = run.sources_onboarded
    if run.memory is not None:
        stats.gate_decisions = (stats.gate_decisions or 0) + run.memory.gate_decisions
    if run.lanes is not None:
        stats.model_calls = run.lanes.calls
        stats.tokens = run.lanes.tokens
    if run.error is not None:
        result = reporter.build(
            end_state=None,
            frame=run.frame,
            nodes=loop.nodes if loop else [],
            chosen=[],
            need=None,
            stats=stats,
            error=run.error,
            **run.common,
        )
        return _with_depth(result, run)
    result = reporter.build(
        end_state=run.end_state,
        frame=run.frame,
        nodes=loop.nodes if loop else [],
        chosen=run.chosen,
        need=run.need,
        stats=stats,
        note=run.note,
        scope=run.scope,
        **run.common,
    )
    lanes = run.lanes
    if (
        lanes is not None
        and lanes.allows("reporter")
        and result.action
        and run.frame
        and not run.scope
    ):
        context = " ".join(
            filter(None, [result.root_cause, result.effect and result.effect.summary])
        )
        wording = model_lanes.draft_action(lanes, result.action, context, run.tracer)
        if wording:
            result.action_template, result.action = result.action, wording
            result.action_source = "model"
        result.stats.model_calls, result.stats.tokens = lanes.calls, lanes.tokens
    return _with_depth(result, run)


def _with_depth(result: RunResult, run: _Run) -> RunResult:
    if run.memory is not None:
        result.memory = run.memory.report()
    if run.lanes is not None:
        result.depth = depth_plan(run.request, run.lanes.switch, unavailable=run.lanes.unavailable)
    return result


def run_loop_a(
    request: RunRequest,
    *,
    source: EvidenceSource | None = None,
    tracer: Tracer | None = None,
    gate: Gate | None = None,
    budgets: LoopBudgets | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    logs_dir: str | Path | None = None,
    clock: Callable[[], float] = time.monotonic,
    lanes: model_lanes.ModelLanes | None = None,
    ask: Ask | None = None,
) -> RunResult:
    """Run Loop A for one request. Never raises for a bad case: failures become a RunResult
    with status "error" (structured) or end state "budget". ``ask`` is the interactive gates'
    channel (used only when hitl_mode is interactive and no ``gate`` is given)."""
    run, source = _start(
        request,
        source=source,
        tracer=tracer,
        gate=gate,
        budgets=budgets,
        run_id=run_id,
        parent_run_id=parent_run_id,
        logs_dir=logs_dir,
        clock=clock,
        lanes=lanes,
        ask=ask,
    )
    try:
        if run.error is None and source is not None:
            _loop_a(run, source)
    finally:
        _finish_live(run)
    return _report(run)


def run_investigation(
    request: RunRequest,
    *,
    source: EvidenceSource | None = None,
    tracer: Tracer | None = None,
    gate: Gate | None = None,
    budgets: LoopBudgets | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    logs_dir: str | Path | None = None,
    clock: Callable[[], float] = time.monotonic,
    discovery: Discovery | None = None,
    zetaris_client: ZetarisClient | None = None,
    data_dir: Path | None = None,
    lanes: model_lanes.ModelLanes | None = None,
    memory_client: Any | None = None,
    ask: Ask | None = None,
) -> RunResult:
    """Loop A, then Loop B when Loop A ends at the evidence frontier with an information need.

    Same keyword arguments and result as ``run_loop_a`` (plus optional ``discovery``, the
    Zetaris client for live registration, the sample directory and the cognition client for
    H-MEM memory, made from COGNITION_URL when not given). Never raises."""
    run, source = _start(
        request,
        source=source,
        tracer=tracer,
        gate=gate,
        budgets=budgets,
        run_id=run_id,
        parent_run_id=parent_run_id,
        logs_dir=logs_dir,
        clock=clock,
        lanes=lanes,
        ask=ask,
    )
    owned = _attach_memory(run, memory_client)
    try:
        _investigate(
            run, source, discovery=discovery, zetaris_client=zetaris_client, data_dir=data_dir
        )
        if _needs_fallback(run, source):
            run.memory.fall_back(run.end_state)  # the shortcut found no cause: test every one
            run.end_state, run.chosen, run.need, run.note = None, [], None, None
            _investigate(
                run,
                source,
                discovery=discovery,
                zetaris_client=zetaris_client,
                data_dir=data_dir,
                again=True,
            )
        _remember(run)
    finally:
        _finish_live(run)
        if owned is not None:
            owned.close()
    return _report(run)


def _needs_fallback(run: _Run, source: EvidenceSource | None) -> bool:
    """A lesson let a level's candidates wait and the run then found no cause."""
    mem = run.memory
    return bool(
        mem is not None
        and mem.skipped
        and not mem.fallback
        and run.error is None
        and run.loop is not None
        and source is not None
        and run.end_state not in memory.SUCCESS_STATES
    )


def _remember(run: _Run) -> None:
    """Feedback for the lessons that held, then the write-back behind the memory gate."""
    if run.memory is None or not run.memory.on:
        return
    try:
        with phase(run.tracer, "remember", owner=memory.AGENT):
            run.memory.finish(
                frame=run.frame,
                nodes=run.loop.nodes if run.loop else [],
                chosen=run.chosen,
                end_state=run.end_state,
                error=run.error is not None,
                scoped=run.scope is not None,
            )
    except Exception as err:  # noqa: BLE001 - memory never fails a run (rule 8)
        run.tracer.emit(
            agent=memory.AGENT,
            action="remember",
            status="degraded",
            output_summary=f"write-back failed: {type(err).__name__}: {err}"[:300],
        )


def _attach_memory(run: _Run, client: Any | None) -> CognitionClient | None:
    """The run's MemoryRun (RunOptions.memory, else MEMORY_MODE). Warm without a client: one
    from COGNITION_URL, returned so the caller closes it. Never raises."""
    mode, why = memory.resolve_mode(run.request.options.memory)
    owned = None
    try:
        if mode == "warm" and client is None and run.error is None:
            client = owned = CognitionClient(
                CognitionConfig.from_env(), run_id=run.common["run_id"], tracer=run.tracer
            )
    except Exception as err:  # noqa: BLE001 - a bad COGNITION_* setting means no memory
        run.tracer.emit(
            agent=memory.AGENT,
            action="off",
            status="degraded",
            output_summary=f"memory off: {type(err).__name__}: {err}"[:300],
        )
        client = None
    run.memory = memory.MemoryRun(mode, client=client, tracer=run.tracer, gate=run.gate)
    if mode == "cold":
        run.memory.announce_cold(why)
    return owned


def _investigate(
    run: _Run,
    source: EvidenceSource | None,
    *,
    discovery: Discovery | None,
    zetaris_client: ZetarisClient | None,
    data_dir: Path | None,
    again: bool = False,
) -> None:
    if run.error is None and source is not None:
        _loop_a(run, source, again=again)
    if (
        run.error is None
        and run.end_state == "evidence-frontier"
        and run.need is not None
        and run.need.entity_hint.relation == BREAKDOWN_RELATION
    ):
        run.tracer.emit(
            agent=AGENT,
            action="loop_b",
            status="rejected",
            node_id=run.need.node_id,
            loop="B",
            output_summary="Loop B skipped: the need is a segment or product breakdown, and no "
            "catalog source holds one (SEC company facts report totals only)",
        )
    elif (
        run.error is None
        and run.end_state == "evidence-frontier"
        and run.need is not None
        and run.frame is not None
        and run.loop is not None
    ):
        try:
            with phase(run.tracer, "loop_b", owner=AGENT) as done:
                LoopB(
                    run,
                    discovery=discovery,
                    zetaris_client=zetaris_client,
                    data_dir=data_dir,
                ).run()
                done.update(end_state=run.end_state)
        except Exception as err:  # noqa: BLE001 - a run must end in a structured result
            _fail(run, err)
    elif run.error is None and run.end_state == "evidence-frontier" and run.need is not None:
        run.tracer.emit(
            agent=AGENT,
            action="loop_b",
            status="rejected",
            node_id=run.need.node_id,
            loop="B",
            output_summary="Loop B skipped: the effect could not be framed, so there is no "
            "baseline to reconcile a new source against",
        )


def _find(obj: Any, attr: str, depth: int = 4) -> Any:
    """An attribute of an evidence source or of the sources it wraps (inner/primary/fallback)."""
    if obj is None or depth < 0:
        return None
    if hasattr(obj, attr):
        return getattr(obj, attr)
    for name in ("inner", "source", "primary", "fallback"):
        found = _find(getattr(obj, name, None), attr, depth - 1)
        if found is not None:
            return found
    return None


class LoopB:
    """gap analyst's need -> source scout -> onboarder (source gate) -> analyst + critic
    reconcile -> asker-relative stopping rule. Bounded by MAX_NEW_SOURCES_PER_RUN and the run's
    wall-clock budget (shared with Loop A). Expected failures (unresolvable hint, gate denial,
    registration failure, missing data) leave the run at the evidence frontier, with the reason
    added to the information need."""

    def __init__(
        self,
        run: _Run,
        *,
        discovery: Discovery | None = None,
        zetaris_client: ZetarisClient | None = None,
        data_dir: Path | None = None,
    ) -> None:
        assert run.loop is not None and run.frame is not None and run.need is not None
        self.run_state = run
        self.loop = run.loop
        self.frame = run.frame
        self.tracer = run.tracer
        self.discovery = discovery
        self.client = zetaris_client
        self.data_dir = data_dir or _find(self.loop.source, "data_dir")
        self.registry = onboarder.Registry(self.loop.source, self.frame.ticker, run.onboarded)

    def _frontier(self, why: str) -> None:
        run = self.run_state
        assert run.need is not None
        run.need = run.need.model_copy(update={"reason": f"{run.need.reason}; Loop B: {why}"})
        self.loop._stop("evidence-frontier", run.chosen, run.need, note=why)
        run.end_state, run.note = "evidence-frontier", why

    def _budget(self, why: str) -> None:
        run = self.run_state
        self.loop._stop("budget", [], run.need, note=why)
        run.end_state, run.chosen, run.note = "budget", [], why

    def run(self) -> None:
        run, need, tracer = self.run_state, self.run_state.need, self.tracer
        assert need is not None
        try:
            self.loop._check_time()
        except _Budget as err:
            return self._budget(str(err))
        tracer.emit(
            agent=AGENT,
            action="loop_b",
            node_id=need.node_id,
            target_agent="source_scout",
            loop="B",
            input_summary=need.test[:300],
            output_summary=f"information need: {need.variable} for the "
            f"{need.entity_hint.relation}; sources cap {run.budgets.max_new_sources}",
        )
        client = self.client
        if not run.sample and client is None:
            client = _find(self.loop.source, "client") or ZetarisClient(
                ZetarisConfig.from_env(), tracer=tracer
            )
        try:
            discovery = (
                self.discovery
                or run.discovery
                or make_discovery(run.sample, tracer, data_dir=self.data_dir, client=client)
            )
        except CatalogError as err:
            return self._frontier(f"catalog unavailable: {err}")
        if run.discovery is None:
            run.discovery = discovery
        with phase(tracer, "scout", owner=source_scout.AGENT) as done:
            scout = source_scout.resolve(need, discovery=discovery, tracer=tracer)
            done.update(outcome="resolved" if scout.resolved else "unresolved")
        chosen = scout.entity.ticker if scout.entity else None
        _pin(run, chosen)  # the Loop B counterparty is never evicted while this run goes on
        _release_discovery(run, discovery, _keep(run, self.frame.ticker, chosen))
        if not scout.resolved:
            return self._frontier(f"source scout could not resolve the hint: {scout.reason}")
        try:
            self.loop._check_time()
        except _Budget as err:
            return self._budget(str(err))
        assert run.gate is not None
        with phase(tracer, "onboard", owner=onboarder.AGENT) as done:
            reg = onboarder.onboard(
                scout,
                need,
                gate=run.gate,
                registry=self.registry,
                cap=run.budgets.max_new_sources,
                sample=run.sample,
                tracer=tracer,
                client=client,
                data_dir=self.data_dir,
                deadline=self.loop.started + run.budgets.run_seconds,
                clock=self.loop.clock,
            )
            done.update(outcome=reg.status)
        self.loop.gate_decisions += int(reg.gate_decided)
        run.sources_onboarded = len(self.registry.onboarded)
        if reg.status == "capped":
            return self._budget(reg.reason)
        if not reg.ok:
            return self._frontier(f"onboarder: {reg.reason}")
        try:
            self.loop._check_time()
        except _Budget as err:
            return self._budget(str(err))
        self._reconcile(scout, reg)

    def _reconcile(self, scout: source_scout.ScoutResult, reg: onboarder.Registration) -> None:
        frame, need, tracer = self.frame, self.run_state.need, self.tracer
        assert need is not None and scout.entity is not None
        entity = scout.entity
        parent = next((n for n in self.loop.nodes if n.node_id == need.node_id), self.loop.nodes[0])
        taken = {n.node_id for n in self.loop.nodes}
        node_id = next(
            f"{parent.node_id}.{i}" for i in range(1, 100) if f"{parent.node_id}.{i}" not in taken
        )
        with phase(tracer, "reconcile", owner=reconciler.ANALYST):
            r = reconciler.measure(
                frame, need, entity, self.registry, node_id=node_id, tracer=tracer
            )
        self.loop.hops += 1
        bound = max_over_explained()
        v, residual = reconciler.judge(frame, r, bound=bound)
        gate_choice = None
        if v.escalation:
            decision = self.loop.gate.decide(
                GateRequest(
                    gate="critic_escalation",
                    question=v.escalation["question"],
                    options=tuple(v.escalation["options"]),
                    proposal=v.escalation["proposal"],
                    node_id=node_id,
                    requested_by=critic.AGENT,
                    context={
                        "share": v.raw_share,
                        "bound": bound,
                        "residual": residual,
                        "entity": entity.name,
                        "cik": entity.cik,
                        "value": r.value,
                        "metric": frame.metric,
                        "period": frame.period_label,
                    },
                )
            )
            self.loop.gate_decisions += 1
            gate_choice = decision.choice
            v = critic.apply_gate(v, decision.choice)
        reconciler.trace_verdict(
            tracer, node_id, r, v, residual, bound=bound, gate_choice=gate_choice
        )
        if v.decision == "prune":
            self.loop.prunes += 1
        events = parent.measurement.events if parent.measurement else []
        value = money(r.value)
        filed = f" filed {events[-1].filing_date}" if events else ""
        claim = (
            f"{entity.name} (CIK {entity.cik}), the separate registrant behind the disposal "
            f"{frame.ticker.upper()} reported in its 8-K{filed}, reports {frame.metric} of "
            f"{value} for {frame.period_label}"
        )
        node = Node(
            node_id=node_id,
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            variable=f"{frame.metric} of {entity.name}",
            claim=claim,
            label=v.label,
            verdict={"accept": "accepted", "prune": "pruned", "frontier": "frontier"}.get(
                v.decision, "pruned"
            ),
            rule="reconcile",
            category=parent.category or "portfolio_change",
            evidence_status=v.evidence_status,
            share=v.share,
            raw_share=v.raw_share,
            effect=r.effect,
            reason=v.reason,
            evidence=[*scout.evidence, *reg.evidence, *r.evidence],
            measurement=Measurement(
                candidate_id=node_id,
                attempt=0,
                scope="window",
                evidence=r.evidence,
                effect=r.effect,
                counterfactual=r.counterfactual,
                events=list(events),
            ),
            context={
                "residual": residual,
                "format": {
                    "counterparty": f"{entity.name} (CIK {entity.cik})",
                    "counterparty_value": value,
                    "residual": residual_text(r.residual_amount, residual),
                },
            },
        )
        self.loop.nodes.append(node)
        if self.run_state.memory is not None:
            self.run_state.memory.observe(node, frame)
        if v.decision != "accept":
            return self._frontier(f"reconcile against {entity.name}: {v.reason}")
        self._stop_rule(node)

    def _stop_rule(self, node: Node) -> None:
        """causal-criteria.md §5: stop at the first accepted cause the asker can act on."""
        run, frame = self.run_state, self.frame
        asker = frame.asker
        causal = [n for n in self.loop.nodes if n.verdict == "accepted" and n.label == "causal"]
        causal.sort(key=lambda n: n.raw_share or 0, reverse=True)
        strong = [n for n in causal if (n.raw_share or 0) >= MOST_OF_EFFECT]
        if len(causal) >= 2 and not strong:
            state, chosen = "branched", causal
        elif actionable_family(node.category or "", list(asker.levers), asker.role):
            state, chosen = "actionable", [node]
        else:
            state, chosen = "external-driver", [node]
        self.loop._stop(state, chosen, None)
        run.end_state, run.chosen, run.need, run.note = state, chosen, None, None


# --- CLI ------------------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    p = Path(path)
    if p.exists() or p.is_absolute():
        return p
    return REPO_ROOT / p


def stdin_ask(request: GateRequest, *, stdin=None, stderr=None) -> Answer:
    """The CLI's interactive channel: print the gate to stderr, read one line from stdin. An
    empty line takes the proposal; at end of input (no answer) the policy decides."""
    stdin, stderr = stdin or sys.stdin, stderr or sys.stderr
    print(
        f"\n[gate {request.gate}] {request.question}\n"
        f"  options: {', '.join(request.options)} (Enter = {request.proposal}) > ",
        end="",
        file=stderr,
        flush=True,
    )
    line = stdin.readline()
    if not line:
        return Answer(choice=None)  # not an option: the Gate falls back to its policy
    choice = line.strip() or request.proposal
    return Answer(choice=choice, note="answered on stdin")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tiresias_agent.orchestrator",
        description="Run an investigation (Loop A, then Loop B) on one request JSON.",
    )
    parser.add_argument("request", help="path to a RunRequest JSON (e.g. input_examples/01_*.json)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="query Zetaris (sample fallback)")
    mode.add_argument("--sample", action="store_true", help="sample mode (the default)")
    parser.add_argument("--loop-a", action="store_true", help="run Loop A only (no Loop B)")
    parser.add_argument("--logs-dir", default=None, help="trace directory (default: <repo>/logs)")
    parser.add_argument(
        "--depth",
        choices=DEPTHS,
        default=None,
        help="investigation depth (ADR-0011): quick = rules only (the default), thorough = + the "
        "critic's model second opinion, deep = every model lane; overrides options.depth",
    )
    parser.add_argument(
        "--memory",
        choices=memory.MODES,
        default=None,
        help="H-MEM memory (ADR-0005): warm = recall and store lessons through COGNITION_URL, "
        "cold = no memory call; overrides options.memory (default MEMORY_MODE, warm)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="answer each human-in-the-loop gate on stdin (hitl_mode interactive; an empty line "
        "takes the proposal); prompts go to stderr",
    )
    args = parser.parse_args(argv)
    try:
        data = json.loads(_resolve(args.request).read_text(encoding="utf-8"))
        request = RunRequest.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as err:
        body = ErrorResponse(error=ErrorDetail(type=type(err).__name__, message=str(err)[:500]))
        print(body.model_dump_json(indent=2))
        return 2
    request.options.sample_mode = not args.live
    if args.depth:
        request.options.depth = args.depth
    if args.memory:
        request.options.memory = args.memory
    ask = None
    if args.interactive:
        request.options.hitl_mode = "interactive"
        ask = stdin_ask
    logs_dir = args.logs_dir or os.getenv("LOGS_DIR") or REPO_ROOT / "logs"
    with contextlib.redirect_stdout(sys.stderr):  # keep stdout for the result only
        run = run_loop_a if args.loop_a else run_investigation
        result = run(request, logs_dir=logs_dir, ask=ask)
    print(result.model_dump_json(indent=2))
    return 0 if result.status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
