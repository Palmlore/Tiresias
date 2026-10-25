"""The reporter's `report` trace line carries the RunResult's end state, root cause and residual.

A committed output is checked against its trace (output_examples/README.md): every field
the check compares must be on that line, not only in the output.
"""

import json

from loop_helpers import request

from tiresias_agent.orchestrator import run_investigation


def test_report_line_carries_root_cause(tmp_path):
    result = run_investigation(
        request("04"), run_id="run-test-report-line", logs_dir=tmp_path, memory_client=False
    )
    lines = [json.loads(line) for line in (tmp_path / "trace-run-test-report-line.jsonl").open()]
    (report,) = [
        line for line in lines if line["agent"] == "reporter" and line["action"] == "report"
    ]
    assert result.root_cause
    assert report["root_cause"] == result.root_cause
    assert report["end_state"] == result.end_state
    assert report["residual"] == result.residual
