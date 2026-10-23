"""Loop A end to end in sample mode on the three input examples, plus budgets and failures."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from loop_helpers import EXAMPLES, RecordingTracer, request

from tiresias_agent import orchestrator
from tiresias_agent.adapters.zetaris import SampleSource
from tiresias_agent.config import LoopBudgets
from tiresias_agent.models import RunResult
from tiresias_agent.orchestrator import run_loop_a

END_STATES = {"actionable", "branched", "cyclic", "evidence-frontier", "external-driver", "budget"}
SRC = Path(orchestrator.__file__).resolve().parent


def _run(prefix, tmp_path, **kw):
    req = request(prefix)
    req.options.sample_mode = True
    result = run_loop_a(req, logs_dir=tmp_path, **kw)
    lines = [json.loads(x) for x in Path(result.trace_file).read_text().splitlines()]
    return result, lines


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = {}
    for path in EXAMPLES:
        prefix = path.name[:2]
        out[prefix] = _run(prefix, tmp_path_factory.mktemp(prefix))
    return out


@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_each_example_reaches_a_valid_end_state(runs, prefix):
    result, _ = runs[prefix]
    assert result.end_state in END_STATES and result.status in {"success", "partial"}
    assert result.action and result.effect is not None and result.effect.delta != 0
    assert RunResult.model_validate(result.model_dump(mode="json")) == result
    dumped = list(result.model_dump().keys())
    assert dumped[:2] == ["end_state", "action"]  # ADR-0009: lead with the decision


@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_every_trace_line_has_run_id_status_and_sample_label(runs, prefix):
    result, lines = runs[prefix]
    assert lines
    for line in lines:
        assert line["run_id"] == result.run_id
        assert line["status"] in {"success", "error", "rejected", "degraded"}
        assert line["mode"] == "sample"


@pytest.mark.parametrize("prefix", ["01", "02", "03"])
def test_agents_interact_more_than_once_and_critic_sends_work_back(runs, prefix):
    _, lines = runs[prefix]
    counts = Counter(line["agent"] for line in lines)
    loop_agents = {"hypothesiser", "analyst", "critic"}
    assert sum(1 for a in loop_agents if counts[a] > 1) >= 2
    send_backs = [
        line
        for line in lines
        if line["agent"] == "critic"
        and (line["action"], line.get("target_agent"))
        in {("retry", "analyst"), ("prune", "hypothesiser")}
    ]
    assert send_backs
    assert any(line["agent"] == "gate" and line["gate_mode"] == "auto" for line in lines)


def test_restatement_cases_show_a_retry_with_a_narrower_query(runs):
    for prefix in ("01", "02"):
        _, lines = runs[prefix]
        retry = next(x for x in lines if x["agent"] == "critic" and x["action"] == "retry")
        node = retry["node_id"]
        measures = [x for x in lines if x["agent"] == "analyst" and x["node_id"] == node]
        assert [m["retry_count"] for m in measures] == [0, 1]
        assert "filing_date" not in (measures[0]["filter"] or "")
        assert "filing_date >" in measures[1]["filter"]
        assert measures[1]["rows"] < measures[0]["rows"]


def test_restatements_end_at_the_evidence_frontier_with_an_information_need(runs):
    for prefix in ("01", "02"):
        result, lines = runs[prefix]
        assert result.end_state == "evidence-frontier"
        need = result.information_need
        assert need is not None and need.variable == result.effect.metric
        assert need.entity_hint.filing_accession and "2.01" in need.entity_hint.filing_items
        assert need.period.start.isoformat() == result.effect.period.start.isoformat()
        assert set(need.join_key) >= {"cik", "period_start", "period_end"}
        assert need.expected_magnitude == pytest.approx(abs(result.effect.delta))
        assert any(x["agent"] == "gap_analyst" for x in lines)


def test_net_loss_case_is_actionable_for_the_board(runs):
    result, _ = runs["03"]
    assert result.end_state == "actionable" and result.information_need is None
    causal = [n for n in result.why_tree if n.label == "causal" and n.verdict == "accepted"]
    assert causal and causal[0].evidence and causal[0].evidence[0].sql
    labels = {n.label for n in result.why_tree}
    assert {"breakdown", "causal", "background"} <= labels


def test_why_tree_links_carry_sql_and_rows(runs):
    for result, _ in runs.values():
        for node in result.why_tree:
            assert node.evidence, node.node_id
            for ev in node.evidence:
                assert ev.sql and ev.row_count >= len(ev.rows)


def test_outputs_differ_across_cases(runs):
    results = [runs[p][0] for p in ("01", "02", "03")]
    assert len({r.action for r in results}) == 3
    assert len({r.root_cause for r in results}) == 3
    assert len({(r.effect.metric, r.effect.kind, r.effect.delta) for r in results}) == 3
    assert len({r.end_state for r in results}) >= 2
    hints = {r.information_need.entity_hint.filing_accession for r in results[:2]}
    assert len(hints) == 2


def test_sample_run_finishes_in_seconds(runs):
    for result, _ in runs.values():
        assert result.stats.seconds < 5 and result.stats.queries > 0


def test_depth_cap_ends_in_budget(tmp_path):
    result, _ = _run("03", tmp_path, budgets=LoopBudgets(max_causal_depth=1))
    assert result.end_state == "budget" and result.status == "partial"
    assert "MAX_CAUSAL_DEPTH" in result.action


def test_wall_clock_budget_ends_in_budget(tmp_path):
    ticks = iter(range(0, 10_000, 100))
    result, _ = _run("01", tmp_path, budgets=LoopBudgets(run_seconds=50), clock=lambda: next(ticks))
    assert result.end_state == "budget"


def test_branch_cap_is_respected(tmp_path):
    result, _ = _run("01", tmp_path, budgets=LoopBudgets(max_branches_per_level=1))
    assert [n.node_id for n in result.why_tree if n.depth == 1] == ["n0.1"]


class ExplodingSource(SampleSource):
    def filings(self, *a, **kw):
        raise RuntimeError("boom")


def test_unexpected_failure_is_a_structured_error_not_a_stack_trace(tmp_path):
    req = request("01")
    result = run_loop_a(req, source=ExplodingSource(), logs_dir=tmp_path)
    assert result.status == "error" and result.error.type == "RuntimeError"
    assert result.end_state is None


def test_invalid_ticker_is_a_structured_error(tmp_path):
    result = run_loop_a(request("01", entity={"ticker": "bad ticker!"}), logs_dir=tmp_path)
    assert result.status == "error" and result.error.type == "InvalidInput"


def test_unknown_entity_is_an_evidence_frontier_for_the_metric(tmp_path):
    result = run_loop_a(request("01", entity={"ticker": "ZZZZ"}), logs_dir=tmp_path)
    assert result.end_state == "evidence-frontier"
    assert result.information_need.variable == "Revenues"
    assert result.information_need.entity_hint.source_ticker == "ZZZZ"


def test_scripted_gate_can_prune_the_frontier(tmp_path):
    req = request("01")
    req.options.hitl_mode = "scripted"
    req.options.decisions = {"critic_escalation": "prune"}
    tracer = RecordingTracer()
    result = run_loop_a(req, tracer=tracer)
    assert result.end_state == "evidence-frontier"  # nothing else explains it either
    gate = next(e for e in tracer.events if e["agent"] == "gate")
    assert (gate["gate_mode"], gate["decided_by"]) == ("scripted", "script")
    node = next(n for n in result.why_tree if n.node_id == gate["node_id"])
    assert node.verdict == "pruned"


def test_cli_prints_result_json_and_writes_trace(tmp_path, capsys):
    example = next(p for p in EXAMPLES if p.name.startswith("03"))
    code = orchestrator.main([str(example), "--logs-dir", str(tmp_path)])
    out = capsys.readouterr().out
    result = RunResult.model_validate_json(out)  # stdout is the result only
    assert code == 0 and result.sample_mode is True
    assert Path(result.trace_file).exists()
    assert re.match(r"trace-run-.*\.jsonl", Path(result.trace_file).name)


def test_cli_bad_input_is_a_structured_error(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert orchestrator.main([str(bad)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


# --- Guard: no case-specific constants in agent logic (AGENTS.md rule 1) -----------------------

FORBIDDEN = [
    r"GEHC",
    r"HealthCare",
    r"Vernova",
    r"\bGEV\b",
    r"\bGE\b",
    r"General Electric",
    r"0000040545",
    r"0001932393",
    r"0001996810",
    r"2023-02-10",
    r"2024-02-02",
    r"2025-02-03",
    r"2023-01-04",
    r"2024-04-02",
    r"76[,_]?555",
    r"58[,_]?100",
    r"22[,_]?355",
    r"18[,_]?455",
    r"67[,_]?954",
    r"35[,_]?348",
    r"22[,_]?136",
    r"0001193125-2",
]
LOGIC_FILES = sorted([*(SRC / "agents").glob("*.py"), SRC / "orchestrator.py", SRC / "gates.py"])


@pytest.mark.parametrize("path", LOGIC_FILES, ids=lambda p: p.name)
def test_agent_logic_has_no_case_specific_literals(path):
    text = path.read_text()
    hits = [p for p in FORBIDDEN if re.search(p, text)]
    assert not hits, f"{path.name} contains case-specific literals: {hits}"
