"""Loop A orchestrator: drill down in the registered sources (docs/architecture.md).

framer -> (hypothesiser -> analyst <-> critic [-> gate])* -> gap analyst? -> reporter

Each level: the hypothesiser proposes up to MAX_BRANCHES_PER_LEVEL candidates for the current
node; the analyst measures each; the critic accepts, prunes (back to the hypothesiser),
sends it back to the analyst with a corrected scope (bounded by MAX_RETRIES_PER_NODE), or
marks it as a frontier. Then the run stops at the first accepted causal link the asker can act
on (causal-criteria.md §5), or digs under the strongest accepted breakdown, up to
MAX_CAUSAL_DEPTH levels and RUN_BUDGET_SECONDS. Every run ends in exactly one end state.

CLI: python -m tiresias_agent.orchestrator <request.json> [--live] [--logs-dir DIR]
prints the RunResult JSON on stdout (trace lines go to stderr and logs/trace-<run_id>.jsonl).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .adapters.zetaris import DEFAULT_FORMS, EvidenceSource, make_source
from .agents import analyst, critic, gap_analyst, hypothesiser, reporter
from .agents.common import MOST_OF_EFFECT, Candidate, Frame, LoopError, Node
from .agents.framer import FrameFailure
from .agents.framer import frame as frame_question
from .agents.framer import summary as frame_summary
from .agents.knowledge import actionable_family
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
        f"seconds={budgets.run_seconds:.0f}",
    )
    loop: LoopA | None = None
    frame: Frame | None = None
    common = {
        "run_id": run_id,
        "trace_file": trace_file,
        "sample_mode": sample,
        "parent_run_id": parent_run_id,
        "tracer": tracer,
    }
    try:
        gate = gate or Gate(
            request.options.hitl_mode or settings.hitl_mode,
            tracer=tracer,
            scripted=request.options.decisions,
        )
        source = source or make_source(sample, tracer)
        loop = LoopA(request, source=source, tracer=tracer, gate=gate, budgets=budgets, clock=clock)
        try:
            frame = frame_question(request, loop.source, tracer)
        except FrameFailure as err:
            tracer.emit(
                agent="framer",
                action="frame",
                status="rejected",
                node_id="n0",
                target_agent="gap_analyst",
                output_summary=str(err),
            )
            ticker = (request.entity.ticker or "").lower()
            need = gap_analyst.for_variable(
                None,
                variable=request.metric,
                node_id="n0",
                reason=str(err),
                tracer=tracer,
                ticker=ticker,
                cik=request.entity.cik,
                period=request.period,
            )
            return reporter.build(
                end_state="evidence-frontier",
                frame=None,
                nodes=[],
                chosen=[],
                need=need,
                stats=loop.stats(),
                **common,
            )
        end_state, chosen, need, note = loop.run(frame)
        return reporter.build(
            end_state=end_state,
            frame=frame,
            nodes=loop.nodes,
            chosen=chosen,
            need=need,
            stats=loop.stats(),
            note=note,
            **common,
        )
    except LoopError as err:
        detail = ErrorDetail(type=err.error_type, message=str(err), recoverable=err.recoverable)
    except Exception as err:  # noqa: BLE001 - a run must end in a structured result
        detail = ErrorDetail(type=type(err).__name__, message=str(err)[:500], recoverable=False)
    tracer.emit(
        agent=AGENT,
        action="error",
        status="error",
        error_type=detail.type,
        output_summary=detail.message,
    )
    return reporter.build(
        end_state=None,
        frame=frame,
        nodes=loop.nodes if loop else [],
        chosen=[],
        need=None,
        stats=loop.stats() if loop else RunStats(),
        error=detail,
        **common,
    )


# --- CLI ------------------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    p = Path(path)
    if p.exists() or p.is_absolute():
        return p
    return REPO_ROOT / p


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tiresias_agent.orchestrator", description="Run Loop A on one request JSON."
    )
    parser.add_argument("request", help="path to a RunRequest JSON (e.g. input_examples/01_*.json)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="query Zetaris (sample fallback)")
    mode.add_argument("--sample", action="store_true", help="sample mode (the default)")
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
        result = run_loop_a(request, logs_dir=logs_dir)
    print(result.model_dump_json(indent=2))
    return 0 if result.status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
