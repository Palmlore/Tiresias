"""JSONL tracing shared with the cognition service (docs/architecture.md#tracing).

One line per action to stdout, and to <logs_dir>/trace-<run_id>.jsonl when a run id is known.
"""

import json
import os
import re
import secrets
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUSES = frozenset({"success", "error", "rejected", "degraded"})
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
REDACTED = "***"
_SECRET_KEY = re.compile(r"token|key|authorization|password", re.IGNORECASE)
_BEARER = re.compile(r"\b(bearer)\s+[^\s'\"]+", re.IGNORECASE)
_RESERVED = frozenset({"timestamp", "service", "run_id", "parent_run_id", "agent", "action"})


def new_run_id() -> str:
    """Sortable, filename-safe run id, e.g. run-20261023T101500Z-a1b2c3."""
    return f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def redact(value: Any) -> Any:
    """Replace values under secret-looking keys (token, key, authorization, password) with ***.

    A string that is a bearer credential becomes ***; one embedded in a longer string is
    masked in place. Numbers are kept, so counters such as tokens=1234 stay readable.
    """
    if isinstance(value, dict):
        return {k: REDACTED if _is_secret(k, v) else redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if value.lower().startswith("bearer "):
            return REDACTED
        return _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", value)
    return value


def _is_secret(key: Any, value: Any) -> bool:
    if not isinstance(key, str) or not _SECRET_KEY.search(key):
        return False
    return value is not None and not isinstance(value, int | float)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Tracer:
    """Writes trace lines for one run."""

    def __init__(
        self,
        run_id: str | None,
        parent_run_id: str | None = None,
        logs_dir: str | Path | None = None,
    ) -> None:
        self.run_id = run_id
        self.parent_run_id = parent_run_id
        self.logs_dir = Path(logs_dir if logs_dir is not None else os.getenv("LOGS_DIR", "logs"))

    @property
    def trace_file(self) -> Path | None:
        if not self.run_id or not RUN_ID_PATTERN.match(self.run_id):
            return None
        return self.logs_dir / f"trace-{self.run_id}.jsonl"

    def emit(self, *, agent: str, action: str, status: str = "success", **fields) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
        event: dict[str, Any] = {
            "timestamp": _now(),
            "service": "agent",
            "run_id": self.run_id,
        }
        if self.parent_run_id:
            event["parent_run_id"] = self.parent_run_id
        event.update(agent=agent, action=action, status=status)
        for key, value in redact(fields).items():
            if key not in _RESERVED:
                event[key] = value
        line = json.dumps(event, default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        path = self.trace_file
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as err:
            failure = {"service": "agent", "event": "trace_write_failed", "error": str(err)}
            sys.stderr.write(json.dumps(failure) + "\n")

    @contextmanager
    def span(self, *, agent: str, action: str, **fields) -> Iterator[dict[str, Any]]:
        """Emit once when the block ends, with duration_ms.

        The yielded dict collects extra fields (and may set "status"). If the block raises,
        the line has status "error" and error_type, and the exception propagates. The measured
        duration_ms always wins over a caller-supplied one.
        """
        extra: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            yield extra
        except BaseException as exc:
            merged = {**fields, **extra, "error_type": type(exc).__name__}
            merged.pop("status", None)
            merged.pop("duration_ms", None)
            self.emit(agent=agent, action=action, status="error", duration_ms=_ms(start), **merged)
            raise
        merged = {**fields, **extra}
        status = merged.pop("status", "success")
        merged.pop("duration_ms", None)
        self.emit(agent=agent, action=action, status=status, duration_ms=_ms(start), **merged)


class NullTracer(Tracer):
    """Same interface as Tracer; writes nothing."""

    def __init__(self, run_id: str | None = None, parent_run_id: str | None = None) -> None:
        super().__init__(run_id, parent_run_id, logs_dir=".")

    @property
    def trace_file(self) -> Path | None:
        return None

    def emit(self, *, agent: str, action: str, status: str = "success", **fields) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}, got {status!r}")


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
