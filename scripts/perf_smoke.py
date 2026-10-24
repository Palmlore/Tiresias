"""Measure real per-phase latency of live investigations against local Zetaris.

Usage (from the repo root; needs a running Zetaris and ZETARIS_API_KEY + SEC_USER_AGENT in .env):
    cd services/agent && uv run python ../../scripts/perf_smoke.py [request.json ...] \
        [--runs 2] [--logs-dir DIR] [--out results.jsonl] [--env-file PATH]

Runs ``orchestrator.run_investigation`` in live mode on each request (default: every
input_examples/*.json, four today), ``--runs`` times in a row: run 1 is "cold" for an entity Zetaris has not
registered yet (registration and the first SEC fetch happen in it), later runs are "warm"
(tables registered, CACHE TABLE hit). Reads per-phase wall time from the run's own trace lines
(``orchestrator`` ``phase_done``), plus the first evidence query, the slowest Zetaris statement
and the longest silence between two trace lines (what a UI spinner has to cover).

Prints one JSON line per run, then a Markdown table. Never prints secrets: the key stays in the
environment and the SEC user agent is masked in every trace line by the adapter.
SEC fair access: runs are sequential, discovery statements are paced at the catalog rate limit
(<= 10/s), and Zetaris sends SEC_USER_AGENT on every fetch.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import pathlib
import sys
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))

PHASES = (
    "resolve_entity",
    "register_entity",
    "frame",
    "loop_a",
    "scout",
    "onboard",
    "reconcile",
    "loop_b",
)


def load_env(env: pathlib.Path) -> None:
    """Same rule as zetaris_probe.py: .env values never override the environment."""
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                value = value.split(" #")[0].strip()
                if value:
                    os.environ.setdefault(key.strip(), value)


def _ts(line: dict) -> float:
    return datetime.fromisoformat(line["timestamp"]).timestamp()


def summarise(lines: list[dict], wall: float, result) -> dict:
    phases: dict[str, int] = {}
    for x in lines:
        if x.get("action") == "phase_done" and x.get("phase") in PHASES:
            phases[x["phase"]] = phases.get(x["phase"], 0) + int(x.get("duration_ms") or 0)
    queries = [x for x in lines if x.get("agent") == "zetaris_adapter" and "duration_ms" in x]
    first = next((x for x in queries if x.get("action") == "concept_series"), None)
    slowest = max(queries, key=lambda x: x["duration_ms"], default=None)
    stamps = [_ts(x) for x in lines]
    silence = max((b - a for a, b in itertools.pairwise(stamps)), default=0.0)
    return {
        "end_state": result.end_state,
        "status": result.status,
        "error": result.error.type if result.error else None,
        "reason": (
            (result.information_need.reason if result.information_need else None)
            or result.root_cause
            or ""
        )[:300],
        "sources_onboarded": result.stats.sources_onboarded,
        "queries": result.stats.queries,
        "phases_ms": phases,
        "first_query_ms": first["duration_ms"] if first else None,
        "slowest_statement": (
            {"label": slowest["action"], "ms": slowest["duration_ms"]} if slowest else None
        ),
        "degraded_lines": sum(1 for x in lines if x.get("status") == "degraded"),
        "max_silence_s": round(silence, 2),
        "trace_lines": len(lines),
        "total_s": round(wall, 2),
    }


def table(rows: list[dict]) -> str:
    def cell(ms):
        return "-" if ms is None else f"{ms / 1000:.1f}"

    head = (
        "| Case | Run | Resolve | Register | First query | Frame | Loop A | Scout | Onboard "
        "| Reconcile | Loop B | Total (s) | Max silence (s) | End state |"
    )
    out = [head, "|" + "---|" * 14]
    for r in rows:
        p = r["phases_ms"]
        out.append(
            f"| {r['case']} | {r['run']} | {cell(p.get('resolve_entity'))} "
            f"| {cell(p.get('register_entity'))} | {cell(r['first_query_ms'])} "
            f"| {cell(p.get('frame'))} | {cell(p.get('loop_a'))} | {cell(p.get('scout'))} "
            f"| {cell(p.get('onboard'))} | {cell(p.get('reconcile'))} | {cell(p.get('loop_b'))} "
            f"| {r['total_s']:.1f} | {r['max_silence_s']:.1f} | {r['end_state'] or r['error']} |"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="perf_smoke.py")
    parser.add_argument("requests", nargs="*", help="RunRequest JSON files")
    parser.add_argument("--runs", type=int, default=2, help="runs per case (1 cold + warm)")
    parser.add_argument("--logs-dir", default=None, help="trace directory (default: logs/perf)")
    parser.add_argument("--out", default=None, help="also append the JSON lines to this file")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between runs")
    parser.add_argument("--env-file", default=str(ROOT / ".env"), help="default: <repo>/.env")
    args = parser.parse_args(argv)

    load_env(pathlib.Path(args.env_file))
    os.environ["ZETARIS_REST_URL"] = os.environ.get(
        "ZETARIS_PROBE_URL", "http://localhost:8888/api/v1.0"
    ).rstrip("/")
    if not os.environ.get("ZETARIS_API_KEY") or not os.environ.get("SEC_USER_AGENT"):
        print("ZETARIS_API_KEY and SEC_USER_AGENT are required (.env)", file=sys.stderr)
        return 2

    from tiresias_agent.models import RunRequest
    from tiresias_agent.orchestrator import run_investigation

    paths = [pathlib.Path(p) for p in args.requests] or sorted(
        (ROOT / "input_examples").glob("*.json")
    )
    logs = pathlib.Path(args.logs_dir or ROOT / "logs" / "perf")
    rows = []
    for path in paths:
        request = RunRequest.model_validate_json(path.read_text(encoding="utf-8"))
        request.options.sample_mode = False
        for n in range(1, max(1, min(args.runs, 5)) + 1):
            start = time.perf_counter()
            with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
                result = run_investigation(request.model_copy(deep=True), logs_dir=logs)
            wall = time.perf_counter() - start
            trace = pathlib.Path(result.trace_file or "")
            lines = (
                [json.loads(x) for x in trace.read_text().splitlines()] if trace.is_file() else []
            )
            row = {
                "case": path.stem,
                "run": "cold" if n == 1 else f"warm{n - 1 if n > 2 else ''}",
                "run_id": result.run_id,
                **summarise(lines, wall, result),
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
            if args.out:
                with open(args.out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
            time.sleep(max(0.0, args.pause))
    print()
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
