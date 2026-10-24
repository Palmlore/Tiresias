"""Run registry: runs Loop A in background threads and feeds their trace lines to subscribers.

Used by app.py for POST /run (synchronous: start, then wait within the run budget), POST /runs
(asynchronous), GET /runs/{run_id} and GET /runs/{run_id}/events (Server-Sent Events). A run
whose hitl_mode is interactive gets a gates.GateChannel (RunRecord.gates): its gates wait on the
run's own thread for GET/POST /runs/{run_id}/gates[/{gate_id}].

Bounded (AGENTS.md rule 7): at most RUNS_REGISTRY_SIZE runs are kept (the oldest finished run
is evicted first), at most MAX_CONCURRENT_RUNS run at once, and each run keeps at most
MAX_TRACE_LINES_PER_RUN lines in memory (the trace file keeps them all).
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import orchestrator
from .config import LoopBudgets, settings
from .gates import GateChannel
from .models import ErrorDetail, RunRequest, RunResult
from .trace import RUN_ID_PATTERN, Tracer, new_run_id

AGENT = "api"
MAX_RUN_ID_LENGTH = 100
WAIT_GRACE_SECONDS = 30.0  # on top of RUN_BUDGET_SECONDS before a waiting caller gives up
KEEPALIVE_SECONDS = 15.0


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except ValueError:
        value = default
    return max(low, min(value, high))


def logs_dir() -> Path:
    """LOGS_DIR, else <repo>/logs (same default as the CLI). Read at call time."""
    return Path(os.getenv("LOGS_DIR") or orchestrator.REPO_ROOT / "logs")


def valid_run_id(run_id: str) -> bool:
    return len(run_id) <= MAX_RUN_ID_LENGTH and bool(RUN_ID_PATTERN.match(run_id))


def execute(
    request: RunRequest, *, tracer: Tracer, run_id: str, logs_dir: Path, ask=None
) -> RunResult:
    """The one place the API calls the investigation pipeline. ``ask`` is the run's
    GateChannel.ask when its hitl_mode is interactive."""
    return orchestrator.run_investigation(
        request, tracer=tracer, run_id=run_id, logs_dir=logs_dir, ask=ask
    )


def hitl_mode(request: RunRequest) -> str:
    """The run's gate mode: RunOptions.hitl_mode, else HITL_MODE (default auto)."""
    return request.options.hitl_mode or settings.hitl_mode


class RegistryBusy(Exception):
    """MAX_CONCURRENT_RUNS runs are already running."""


class RunConflict(Exception):
    """A run with this id is already in the registry."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class RunRecord:
    run_id: str
    max_lines: int
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    result: RunResult | None = None
    error: ErrorDetail | None = None  # set when the pipeline itself raised
    lines: list[str] = field(default_factory=list)
    dropped_lines: int = 0
    trace_file: str | None = None
    hitl_mode: str = "auto"
    gates: GateChannel | None = None  # set when hitl_mode is interactive
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    @property
    def status(self) -> str:
        return "done" if self.done else "running"

    def append(self, line: str) -> None:
        with self._cond:
            if len(self.lines) < self.max_lines:
                self.lines.append(line)
            else:
                self.dropped_lines += 1
            self._cond.notify_all()

    def finish(self, result: RunResult | None, error: ErrorDetail | None) -> None:
        with self._cond:
            self.result, self.error = result, error
            self.finished_at = _now()
            self._cond.notify_all()

    def wait(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self.done, timeout=timeout)

    def follow(
        self, *, keepalive: float = KEEPALIVE_SECONDS, max_seconds: float | None = None
    ) -> Iterator[str | None]:
        """Yield every kept line from the start, then live ones, until the run is done.

        Yields None after `keepalive` seconds without a line (the caller sends a comment), and
        stops after `max_seconds` even if the run is still going.
        """
        sent = 0
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        while True:
            with self._cond:
                if sent >= len(self.lines) and not self.done:
                    self._cond.wait(timeout=keepalive)
                batch = self.lines[sent:]
                finished = self.done
            sent += len(batch)
            if batch:
                yield from batch
            elif not finished:
                yield None
            if finished and sent >= len(self.lines):
                return
            if deadline is not None and time.monotonic() > deadline:
                return

    def summary(self) -> dict:
        """GET /runs/{run_id} body (contracts/openapi/agent.yaml#RunStatus)."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "events": len(self.lines),
            "dropped_events": self.dropped_lines,
            "trace_file": self.trace_file,
            "hitl_mode": self.hitl_mode,
            "waiting_gate": self.gates.waiting() if self.gates else None,
            "result": self.result.model_dump(mode="json") if self.result else None,
            "error": self.error.model_dump() if self.error else None,
        }


class RunRegistry:
    """In-memory, bounded registry of runs (process-local; lost on restart)."""

    def __init__(
        self,
        *,
        max_runs: int | None = None,
        max_concurrent: int | None = None,
        max_lines: int | None = None,
        runner: Callable[..., RunResult] | None = None,
    ) -> None:
        self.max_runs = max_runs or _bounded_env_int("RUNS_REGISTRY_SIZE", 50, 1, 500)
        concurrent = max_concurrent or _bounded_env_int("MAX_CONCURRENT_RUNS", 4, 1, 16)
        self.max_concurrent = min(concurrent, self.max_runs)
        self.max_lines = max_lines or _bounded_env_int("MAX_TRACE_LINES_PER_RUN", 5000, 100, 50000)
        self._runner = runner
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def running(self) -> int:
        with self._lock:
            return sum(1 for r in self._runs.values() if not r.done)

    def start(self, request: RunRequest, run_id: str | None = None) -> RunRecord:
        """Register a run and start it in a daemon thread. Raises RegistryBusy or RunConflict."""
        run_id = run_id or new_run_id()
        record = RunRecord(run_id=run_id, max_lines=self.max_lines, hitl_mode=hitl_mode(request))
        with self._lock:
            if run_id in self._runs:
                raise RunConflict(run_id)
            if sum(1 for r in self._runs.values() if not r.done) >= self.max_concurrent:
                raise RegistryBusy(self.max_concurrent)
            self._evict()
            self._runs[run_id] = record
        thread = threading.Thread(
            target=self._work, args=(record, request), name=f"run-{run_id}", daemon=True
        )
        thread.start()
        return record

    def _evict(self) -> None:
        """Drop the oldest finished runs until there is room for one more (lock held)."""
        while len(self._runs) >= self.max_runs:
            oldest = next((k for k, r in self._runs.items() if r.done), None)
            if oldest is None:  # unreachable while max_concurrent <= max_runs
                raise RegistryBusy(self.max_concurrent)
            del self._runs[oldest]

    def _work(self, record: RunRecord, request: RunRequest) -> None:
        sample = request.options.sample_mode
        sample = settings.sample_mode if sample is None else sample
        directory = logs_dir()
        tracer = Tracer(
            record.run_id,
            logs_dir=directory,
            defaults={"mode": "sample" if sample else "live"},
            sink=record.append,
        )
        record.trace_file = str(tracer.trace_file) if tracer.trace_file else None
        tracer.emit(agent=AGENT, action="run_accepted", input_summary=request.question[:200])
        result: RunResult | None = None
        error: ErrorDetail | None = None
        try:
            runner = self._runner or execute
            kwargs = {"tracer": tracer, "run_id": record.run_id, "logs_dir": directory}
            if record.hitl_mode == "interactive":
                # The run's gates wait on its own thread for POST /runs/{id}/gates/{gate_id},
                # each at most GATE_TIMEOUT_SECONDS and never past the run budget.
                record.gates = GateChannel(
                    tracer=tracer,
                    run_deadline=time.monotonic() + LoopBudgets.from_env().run_seconds,
                )
                kwargs["ask"] = record.gates.ask
            result = runner(request, **kwargs)
        except Exception as err:  # noqa: BLE001 - a crash becomes a structured error, not a trace
            error = ErrorDetail(type=type(err).__name__, message=str(err)[:500], recoverable=False)
            tracer.emit(
                agent=AGENT,
                action="run_failed",
                status="error",
                error_type=error.type,
                output_summary=error.message,
            )
        else:
            tracer.emit(
                agent=AGENT,
                action="run_finished",
                status="error" if result.status == "error" else "success",
                output_summary=f"end_state={result.end_state} status={result.status}",
            )
        finally:
            record.finish(result, error)


def wait_budget_seconds() -> float:
    """How long POST /run waits for a run: RUN_BUDGET_SECONDS plus a grace period."""
    return LoopBudgets.from_env().run_seconds + WAIT_GRACE_SECONDS
