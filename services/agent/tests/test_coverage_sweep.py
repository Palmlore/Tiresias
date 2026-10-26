"""scripts/coverage_sweep.py: a company that fails is recorded, never skipped (PR #46)."""

import json
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unreachable_api_records_a_row_per_company(tmp_path):
    out = tmp_path / "results.jsonl"
    api = f"http://127.0.0.1:{_closed_port()}"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/coverage_sweep.py"),
            "--api",
            api,
            "--out",
            str(out),
            "--only",
            "JNJ,KHC,AAPL",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["ticker"] for r in rows] == ["JNJ", "KHC", "AAPL"]
    assert rows[0]["outcome"] == "not resolved"
    assert rows[0]["zetaris_before"].startswith("health")
    assert rows[2]["outcome"] == "error"


def test_rerun_replaces_the_output_unless_append(tmp_path):
    out = tmp_path / "results.jsonl"
    api = f"http://127.0.0.1:{_closed_port()}"
    cmd = [sys.executable, str(ROOT / "scripts/coverage_sweep.py"), "--api", api, "--out", str(out)]
    for extra in ([], [], ["--append"]):
        subprocess.run(
            [*cmd, "--only", "KHC", *extra], check=True, capture_output=True, timeout=120
        )
    tickers = [json.loads(line)["ticker"] for line in out.read_text().splitlines()]
    assert tickers == ["KHC", "KHC"]  # the second run replaced the first; --append added one
