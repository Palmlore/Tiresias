#!/usr/bin/env python3
"""Memory demo against a running stack: cold -> warm -> a misapplied lesson -> purge -> cold.

Usage (the stack running, e.g. `docker compose up --build`; stdlib only, Python 3.11):
    python3 scripts/memory_demo.py [--agent http://localhost:8000] [--live]

Steps, each one HTTP call to the agent (POST /run with options.memory "warm", sample mode unless
--live, so a judge's run is deterministic and needs no keys):
  0. POST /memory/purge                     start from empty memory
  A. 04 (3M -> Solventum spin-off)          nothing to recall; stores the spin-off lesson
  B. 01 (GE -> GE HealthCare spin-off)      recalls it: the disposal is tested first, the other
                                            two candidates wait -> fewer hops and queries
  C. 03 (GE 2018 goodwill impairment)       recalls the spin-off lesson too (broad recall); the
                                            critic prunes its step and rejects it (not_helpful)
  4. POST /memory/purge                     the purge cognition confirmed
  B'. 01 again                              empty memory again: as many hops as a cold run

Prints one line per step (end state, hops, queries, recalled ids with the critic's call, skipped
candidates, feedback, lesson stored) and the comparison. Exit 0 only if the warm run took fewer
hops than the cold one, the impairment run rejected the recalled lesson, every end state is
unchanged (actionable) and the purge removed at least one lesson.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "input_examples"
CASES = {
    "A": "04_3m_2023_revenue_restated.json",
    "B": "01_ge_2022_revenue_restated.json",
    "C": "03_ge_2018_net_loss.json",
}


def call(base: str, path: str, body: dict | None, run_id: str, timeout: float) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Run-Id": run_id},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read())
        except ValueError:
            return err.code, {"error": {"message": str(err)}}


def run_case(base: str, key: str, run_id: str, live: bool, timeout: float) -> dict:
    body = json.loads((EXAMPLES / CASES[key]).read_text())
    body.setdefault("options", {})
    body["options"]["memory"] = "warm"
    if not live:
        body["options"]["sample_mode"] = True
    status, result = call(base, "/run", body, run_id, timeout)
    if status != 200:
        raise SystemExit(f"{run_id}: HTTP {status}: {result.get('error')}")
    return result


def line(step: str, label: str, r: dict) -> str:
    m = r.get("memory") or {}
    s = r.get("stats") or {}
    recalled = [f"{x['memory_id']}:{x['status']}({x.get('relevance')})" for x in m.get("recalled", [])]
    feedback = [f"{f['memory_id']}:{f['kind']}" for f in m.get("feedback", [])]
    stored = m.get("stored") or {}
    kept = stored.get("memory_id") if stored.get("stored") else None
    return (
        f"{step:<2} {label:<34} end_state={r.get('end_state')} hops={s.get('hops')} "
        f"queries={s.get('queries')} recalled={recalled or '-'} "
        f"skipped={[x['node_id'] for x in m.get('skipped', [])] or '-'} "
        f"feedback={feedback or '-'} stored={kept or '-'}"
        + ("" if m.get("available", True) is not False else " MEMORY UNAVAILABLE")
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent", default="http://localhost:8000", help="agent base URL")
    ap.add_argument("--live", action="store_true", help="do not force sample mode")
    ap.add_argument("--timeout", type=float, default=660.0, help="seconds per call")
    args = ap.parse_args(argv)
    tag = f"memdemo-{time.strftime('%Y%m%dT%H%M%S')}"

    status, purge0 = call(args.agent, "/memory/purge", {"requested_by": "memory_demo"}, f"{tag}-purge0", 30)
    if status != 200:
        print(f"0  purge failed: HTTP {status} {purge0.get('error')}")
        return 1
    print(f"0  purge                              purged={purge0['purged']} (start from empty memory)")
    a = run_case(args.agent, "A", f"{tag}-A", args.live, args.timeout)
    print(line("A", "04 3M spin-off, empty memory", a))
    b = run_case(args.agent, "B", f"{tag}-B", args.live, args.timeout)
    print(line("B", "01 GE spin-off, warm", b))
    c = run_case(args.agent, "C", f"{tag}-C", args.live, args.timeout)
    print(line("C", "03 GE impairment, warm", c))
    status, purge = call(args.agent, "/memory/purge", {"requested_by": "memory_demo"}, f"{tag}-purge", 30)
    if status != 200:
        print(f"4  purge failed: HTTP {status} {purge.get('error')}")
        return 1
    print(f"4  purge                              purged={purge['purged']} (cognition confirmed)")
    b2 = run_case(args.agent, "B", f"{tag}-B2", args.live, args.timeout)
    print(line("B'", "01 GE spin-off, after purge", b2))

    hops = lambda r: (r.get("stats") or {}).get("hops")  # noqa: E731
    queries = lambda r: (r.get("stats") or {}).get("queries")  # noqa: E731
    lesson = ((a.get("memory") or {}).get("stored") or {}).get("memory_id")
    rejected = (c.get("memory") or {}).get("rejected", [])
    checks = {
        "warm B took fewer hops than B after the purge": hops(b) < hops(b2),
        "warm B made fewer queries than B after the purge": queries(b) < queries(b2),
        "C rejected the recalled spin-off lesson": bool(lesson) and lesson in rejected,
        "every end state is actionable": all(r.get("end_state") == "actionable" for r in (a, b, c, b2)),
        "B and B' reach the same root cause": b.get("root_cause") == b2.get("root_cause"),
        "the purge removed at least one lesson": purge["purged"] >= 1,
    }
    print(
        f"\ncold vs warm (01): hops {hops(b2)} -> {hops(b)}, queries {queries(b2)} -> {queries(b)}; "
        f"lesson {lesson} rejected on 03: {lesson in rejected}"
    )
    for name, ok in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {name}")
    print(f"traces: logs/trace-{tag}-*.jsonl")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
