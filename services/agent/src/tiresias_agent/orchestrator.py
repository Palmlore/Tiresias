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

CLI: python -m tiresias_agent.orchestrator <request.json> [--live] [--loop-a] [--logs-dir DIR]
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
from .adapters.zetaris import (
    DEFAULT_FORMS,
    EvidenceSource,
    ZetarisClient,
    ZetarisConfig,
    make_source,
)
from .agents import (
    analyst,
    critic,
    gap_analyst,
    hypothesiser,
    onboarder,
    reconciler,
    reporter,
    source_scout,
)
from .agents.common import MOST_OF_EFFECT, Candidate, Frame, LoopError, Measurement, Node
from .agents.framer import FrameFailure
from .agents.framer import frame as frame_question
from .agents.framer import summary as frame_summary
from .agents.knowledge import actionable_family, money, residual_text
from .config import LoopBudgets, settings
from .gates import Gate, GateRequest
from .models import (
    ErrorDetail,
    ErrorResponse,
    InformationNeed,
    Period,
    RunRequest,
    RunResult,
    RunStats,
)
from .trace import Tracer, new_run_id

AGENT = "orchestrator"
REPO_ROOT = Path(__file__).resolve().parents[4]


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
    ) -> None:
        self.request = request
        self.source = CountingSource(source)
        self.tracer = tracer
        self.gate = gate
        self.budgets = budgets
        self.clock = clock
        self.started = clock()
        self.nodes: list[Node] = []
        self.hops = self.retries = self.prunes = self.gate_decisions = 0

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
                        context={"share": v.raw_share},
                    )
                )
                self.gate_decisions += 1
                v = critic.apply_gate(v, decision.choice)
            critic.trace_verdict(self.tracer, cand, m, v)
            if v.decision == "retry":
                attempt += 1
                self.retries += 1
                scope = v.retry_scope or scope
                continue
            break
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
            evidence=[frame.evidence],
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
                )
                if not cands:
                    return self._no_more_rules(frame, parent, frontier)
                level_nodes = []
                for cand in cands:
                    node = self._test(frame, cand)
                    tested.add(cand.variable)
                    self.nodes.append(node)
                    level_nodes.append(node)
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
    run = _Run(request, tracer, budgets, sample, common, clock)
    try:
        run.gate = gate or Gate(
            request.options.hitl_mode or settings.hitl_mode,
            tracer=tracer,
            scripted=request.options.decisions,
        )
        source = source or make_source(sample, tracer)
    except Exception as err:  # noqa: BLE001 - a run must end in a structured result
        _fail(run, err)
    return run, source


def _loop_a(run: _Run, source: EvidenceSource) -> None:
    """Framer + Loop A. Records the outcome on ``run``; never raises."""
    request, tracer = run.request, run.tracer
    try:
        assert run.gate is not None
        run.loop = LoopA(
            request,
            source=source,
            tracer=tracer,
            gate=run.gate,
            budgets=run.budgets,
            clock=run.clock,
        )
        try:
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
            run.end_state = "evidence-frontier"
            run.need = gap_analyst.for_variable(
                None,
                variable=request.metric,
                node_id="n0",
                reason=str(err),
                tracer=tracer,
                ticker=(request.entity.ticker or "").lower(),
                cik=request.entity.cik,
                period=request.period,
            )
            return
        run.end_state, run.chosen, run.need, run.note = run.loop.run(run.frame)
    except Exception as err:  # noqa: BLE001 - LoopError or a bug: a structured result either way
        _fail(run, err)


def _report(run: _Run) -> RunResult:
    loop = run.loop
    stats = loop.stats() if loop else RunStats()
    stats.sources_onboarded = run.sources_onboarded
    if run.error is not None:
        return reporter.build(
            end_state=None,
            frame=run.frame,
            nodes=loop.nodes if loop else [],
            chosen=[],
            need=None,
            stats=stats,
            error=run.error,
            **run.common,
        )
    return reporter.build(
        end_state=run.end_state,
        frame=run.frame,
        nodes=loop.nodes if loop else [],
        chosen=run.chosen,
        need=run.need,
        stats=stats,
        note=run.note,
        **run.common,
    )


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
) -> RunResult:
    """Run Loop A for one request. Never raises for a bad case: failures become a RunResult
    with status "error" (structured) or end state "budget"."""
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
    )
    if run.error is None and source is not None:
        _loop_a(run, source)
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
) -> RunResult:
    """Loop A, then Loop B when Loop A ends at the evidence frontier with an information need.

    Same keyword arguments and result as ``run_loop_a`` (plus optional ``discovery``, the
    Zetaris client for live registration, and the sample directory). Never raises."""
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
    )
    if run.error is None and source is not None:
        _loop_a(run, source)
    if (
        run.error is None
        and run.end_state == "evidence-frontier"
        and run.need is not None
        and run.frame is not None
        and run.loop is not None
    ):
        try:
            LoopB(
                run,
                discovery=discovery,
                zetaris_client=zetaris_client,
                data_dir=data_dir,
            ).run()
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
    return _report(run)


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
        self.registry = onboarder.Registry(self.loop.source, self.frame.ticker)

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
        try:
            discovery = self.discovery or make_discovery(run.sample, tracer, data_dir=self.data_dir)
        except CatalogError as err:
            return self._frontier(f"catalog unavailable: {err}")
        scout = source_scout.resolve(need, discovery=discovery, tracer=tracer)
        if not scout.resolved:
            return self._frontier(f"source scout could not resolve the hint: {scout.reason}")
        try:
            self.loop._check_time()
        except _Budget as err:
            return self._budget(str(err))
        client = self.client
        if not run.sample and client is None:
            client = _find(self.loop.source, "client") or ZetarisClient(
                ZetarisConfig.from_env(), tracer=tracer
            )
        assert run.gate is not None
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
        )
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
        r = reconciler.measure(frame, need, entity, self.registry, node_id=node_id, tracer=tracer)
        self.loop.hops += 1
        v, residual = reconciler.judge(frame, r)
        if v.escalation:
            decision = self.loop.gate.decide(
                GateRequest(
                    gate="critic_escalation",
                    question=v.escalation["question"],
                    options=tuple(v.escalation["options"]),
                    proposal=v.escalation["proposal"],
                    node_id=node_id,
                    requested_by=critic.AGENT,
                    context={"share": v.raw_share},
                )
            )
            self.loop.gate_decisions += 1
            v = critic.apply_gate(v, decision.choice)
        reconciler.trace_verdict(tracer, node_id, r, v, residual)
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
    args = parser.parse_args(argv)
    try:
        data = json.loads(_resolve(args.request).read_text(encoding="utf-8"))
        request = RunRequest.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as err:
        body = ErrorResponse(error=ErrorDetail(type=type(err).__name__, message=str(err)[:500]))
        print(body.model_dump_json(indent=2))
        return 2
    request.options.sample_mode = not args.live
    logs_dir = args.logs_dir or os.getenv("LOGS_DIR") or REPO_ROOT / "logs"
    with contextlib.redirect_stdout(sys.stderr):  # keep stdout for the result only
        run = run_loop_a if args.loop_a else run_investigation
        result = run(request, logs_dir=logs_dir)
    print(result.model_dump_json(indent=2))
    return 0 if result.status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
