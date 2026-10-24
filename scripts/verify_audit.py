#!/usr/bin/env python3
"""Re-run the verifier audit (docs/measurements/verifier-audit-2026-10-24.md) and check every row's class.

Reads docs/measurements/verifier-audit-2026-10-24.criteria.json (one record per acceptance criterion of
the merged PRs), runs each command from the repo root with a timeout, and checks the recorded class:

  pass          the command exits 0; every pytest run in it selected and passed at least one test
                and none failed; otherwise "regressed"
  fail          the command exits 0, i.e. the recorded contradiction is still there; otherwise
                "changed" (the fail was fixed)
  superseded    the new-location command passes (same rules as pass); otherwise "broken"
  not-runnable  reported, not run

Observed "fail" therefore only ever means a recorded, still-present contradiction. A "regressed"
row is a recorded pass that no longer holds: either a real failure or a claim a later merge changed
on purpose; deciding which is a human call, recorded by re-classifying the row in the JSON
(superseded + new_location_command). A row whose tools are missing (node, npx) or that needs the
cognition server without --with-cognition is reported "skipped" and is not a mismatch. Exit 1 if
any row's observed class differs from the recorded one.

Python commands use `<python> -m pytest` / `<python> -m ruff` with PYTHONPATH=services/agent/src;
<python> is --python (default: this interpreter). No uv, no network, no gh, except:
  --refresh  re-extract the criteria from the PR bodies with `gh` and report any difference from the
             JSON (the JSON is not rewritten).
Stdlib only, Python 3.11.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITERIA = ROOT / "docs/measurements/verifier-audit-2026-10-24.criteria.json"
CLASSES = ("pass", "fail", "superseded", "not-runnable")
SUMMARY_RE = re.compile(r"^=*\s*((?:\d+ (?:passed|failed|skipped|deselected|errors?|warnings?|xfailed|xpassed)(?:, )?)+) in [\d.]+s")
REPO = "Palmlore/Tiresias"


def pytest_ok(cmd: str, output: str) -> tuple[bool, str]:
    """Every pytest invocation in the command selected and passed >= 1 test, none failed."""
    expected = len(re.findall(r"-m pytest\b", cmd))
    if not expected:
        return True, ""
    if "no tests ran" in output:
        return False, "no tests ran (selector matched nothing)"
    summaries = [m.group(1) for line in output.splitlines() if (m := SUMMARY_RE.match(line.strip()))]
    if len(summaries) < expected:
        return False, f"{len(summaries)} pytest summaries for {expected} pytest runs"
    for s in summaries:
        counts = {k: int(n) for n, k in re.findall(r"(\d+) (\w+)", s)}
        if counts.get("passed", 0) < 1 or counts.get("failed", 0) or counts.get("error", 0) or counts.get("errors", 0):
            return False, s
    return True, "; ".join(summaries)


def run(cmd: str, env: dict, timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run(["bash", "-c", cmd], check=False, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    out = r.stdout + r.stderr
    ok, detail = pytest_ok(cmd, out)
    if r.returncode != 0:
        tail = [line for line in out.strip().splitlines() if line.strip()][-1:] or [""]
        return False, f"exit {r.returncode}: {tail[0][:160]}"
    if not ok:
        return False, detail
    if not detail:
        lines = [line for line in out.strip().splitlines() if line.strip()]
        detail = f"exit 0{': ' + lines[-1][:120] if lines else ''}"
    return True, detail


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_cognition(tmp: Path) -> tuple[subprocess.Popen, int]:
    port = free_port()
    env = dict(os.environ, COGNITION_PORT=str(port), MEMORY_DIR=str(tmp / "memory"), LOGS_DIR=str(tmp / "cognition-logs"))
    proc = subprocess.Popen(
        ["node", "--experimental-transform-types", "src/server.ts"],
        cwd=ROOT / "services/cognition",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                return proc, port
        except OSError:
            time.sleep(0.3)
    proc.terminate()
    raise RuntimeError("cognition server did not answer /health within 20 s")


def extract(body: str) -> list[dict]:
    body = body.replace("\r\n", "\n")
    m = re.search(r"## ACCEPTANCE CRITERIA(.*?)(\n## |\Z)", body, re.DOTALL)
    out, cur, last = [], None, "s"
    for line in (m.group(1) if m else "").split("\n"):
        s = line.strip()
        if re.match(r"^([-*]|\d+\.)\s*\[C\]", s):
            cur = {"s": re.sub(r"^([-*]|\d+\.)\s*\[C\]\s*", "", s), "f": "", "v": ""}
            out.append(cur)
            last = "s"
        elif s.startswith("FALSIFIED-BY:") and cur:
            cur["f"], last = s[len("FALSIFIED-BY:") :].strip(), "f"
        elif s.startswith("VERIFIER:") and cur:
            cur["v"], last = s[len("VERIFIER:") :].strip(), "v"
        elif s and cur:
            cur[last] += " " + s
    return out


def refresh(data: dict) -> int:
    if not shutil.which("gh"):
        print("--refresh needs the gh CLI", file=sys.stderr)
        return 2
    rows = {r["id"]: r for r in data["criteria"]}
    diffs = 0
    for n in data["prs"]:
        body = subprocess.run(
            ["gh", "pr", "view", str(n), "-R", REPO, "--json", "body", "-q", ".body"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
        got = extract(body)
        want = [r for r in data["criteria"] if r["pr"] == n]
        if len(got) != len(want):
            print(f"#{n}: {len(got)} criteria in the PR body, {len(want)} in the JSON")
            diffs += 1
        for i, c in enumerate(got, 1):
            r = rows.get(f"{n}.{i}")
            if r is None:
                continue
            for key, field in (("s", "statement"), ("f", "falsified_by"), ("v", "original_verifier")):
                if c[key] != r[field]:
                    print(f"#{n}.{i}: {field} differs from the PR body")
                    diffs += 1
    print(f"refresh: {len(data['prs'])} PR bodies read, {diffs} difference(s)")
    return 1 if diffs else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default=sys.executable, help="interpreter with pytest, ruff, fastapi, httpx, pydantic (default: this one)")
    ap.add_argument("--criteria", default=str(CRITERIA))
    ap.add_argument("--with-cognition", action="store_true", help="start the cognition server (node) for #3.7")
    ap.add_argument("--only", action="append", default=[], help="run only these row ids (e.g. 8.4); repeatable")
    ap.add_argument("--timeout", type=int, default=300, help="seconds per command (default 300)")
    ap.add_argument("--refresh", action="store_true", help="re-extract criteria from the PR bodies with gh and diff them")
    args = ap.parse_args()

    data = json.loads(Path(args.criteria).read_text(encoding="utf-8"))
    if args.refresh:
        return refresh(data)

    tmp = Path(tempfile.mkdtemp(prefix="verify-audit-"))
    env = {k: v for k, v in os.environ.items() if k != "COGNITION_URL"}
    src = str(ROOT / "services/agent/src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["AUDIT_TMP"] = str(tmp)
    python = os.path.abspath(args.python) if os.sep in args.python else (shutil.which(args.python) or args.python)
    try:
        subprocess.run([python, "-c", "import pytest, ruff"], check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        print(f"{args.python}: not a Python with pytest and ruff installed", file=sys.stderr)
        return 2
    args.python = python
    py = shlex.quote(python)

    cognition = None
    rows = [r for r in data["criteria"] if not args.only or r["id"] in args.only]
    if args.with_cognition and any("cognition" in r["requires"] for r in rows):
        if not shutil.which("node"):
            print("--with-cognition needs node", file=sys.stderr)
            return 2
        cognition, port = start_cognition(tmp)
        env["COGNITION_PORT"] = str(port)

    observed: Counter = Counter()
    mismatches = []
    try:
        for r in rows:
            want = r["expected_class"]
            missing = [t for t in r["requires"] if t in ("node", "npx") and not shutil.which(t)]
            if want == "not-runnable":
                got, detail = "not-runnable", r["adapted"] or "not run"
            elif missing:
                got, detail = "skipped", f"needs {', '.join(missing)}"
            elif "cognition" in r["requires"] and cognition is None:
                got, detail = "skipped", "needs the cognition server (--with-cognition)"
            else:
                cmd = r["new_location_command"] if want == "superseded" else r["command"]
                ok, detail = run(cmd.replace("{py}", py), env, args.timeout)
                if want == "pass":
                    got = "pass" if ok else "regressed"
                elif want == "fail":
                    got = "fail" if ok else "changed"
                else:
                    got = "superseded" if ok else "broken"
            observed[got] += 1
            flag = "ok  " if got in (want, "skipped") else "DIFF"
            if flag == "DIFF":
                mismatches.append(r["id"])
            print(f"{flag} #{r['id']:<6} recorded={want:<13} observed={got:<13} {detail}", flush=True)
    finally:
        if cognition is not None:
            cognition.terminate()
            cognition.wait(timeout=10)

    recorded = Counter(r["expected_class"] for r in rows)
    fmt = lambda c: " ".join(
        f"{k}={c.get(k, 0)}" for k in (*CLASSES, "skipped", "regressed", "changed", "broken") if c.get(k, 0) or k in CLASSES
    )
    print(f"\nrows: {len(rows)}  python: {args.python}")
    print(f"recorded: {fmt(recorded)}")
    print(f"observed: {fmt(observed)}")
    if mismatches:
        print(f"MISMATCH in {len(mismatches)} row(s): {', '.join(mismatches)}")
        if observed.get("regressed"):
            print(
                "regressed rows: a recorded pass no longer holds; decide whether it is a real fail or a claim a later merge changed on purpose (superseded)"
            )
        return 1
    print("all rows match the recorded classes" + (" (skipped rows not checked)" if observed.get("skipped") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
