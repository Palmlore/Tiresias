"""HTTP API: POST /run (sync), POST /runs + GET /runs/{id} (async), SSE events, errors."""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loop_helpers import EXAMPLES

from tiresias_agent import app as app_module
from tiresias_agent import runs
from tiresias_agent.models import RunResult
from tiresias_agent.runs import RunRegistry


def _body(path: Path) -> dict:
    data = json.loads(path.read_text())
    data.setdefault("options", {})["sample_mode"] = True
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "registry", RunRegistry(max_runs=10, max_concurrent=4))
    return TestClient(app_module.app, raise_server_exceptions=False)


def _error_shape(body: dict) -> None:
    assert body["status"] == "error"
    assert set(body["error"]) == {"type", "message", "recoverable"}
    assert "Traceback" not in json.dumps(body)


def test_run_on_all_three_examples_returns_distinct_valid_results(client):
    results = []
    for path in EXAMPLES:
        response = client.post("/run", json=_body(path))
        assert response.status_code == 200, response.text
        result = RunResult.model_validate(response.json())
        assert result.status in {"success", "partial"} and result.end_state
        assert response.headers["X-Run-Id"] == result.run_id
        assert Path(result.trace_file).exists()
        results.append(result)
    assert len(EXAMPLES) == 3
    assert len({r.run_id for r in results}) == 3
    assert len({(r.effect.delta, r.root_cause, r.end_state) for r in results}) == 3


def test_run_goes_through_loop_b_for_a_restatement(client):
    # /run calls run_investigation: case 01 leaves Loop A at the frontier and Loop B reconciles it.
    response = client.post("/run", json=_body(EXAMPLES[0]))
    assert response.status_code == 200, response.text
    result = RunResult.model_validate(response.json())
    assert result.end_state == "actionable"
    assert any(link.rule == "reconcile" for link in result.why_tree)
    assert result.residual is not None and abs(result.residual) < 0.05


def test_bad_body_is_a_structured_422(client):
    response = client.post("/run", json={"question": "why?"})
    assert response.status_code == 422
    body = response.json()
    _error_shape(body)
    assert body["error"]["type"] == "ValidationError"
    assert "entity" in body["error"]["message"] and "detail" not in body


def test_malformed_json_is_a_structured_422(client):
    response = client.post(
        "/run", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    _error_shape(response.json())


def test_invalid_run_id_header_is_a_structured_422(client):
    response = client.post("/run", json=_body(EXAMPLES[0]), headers={"X-Run-Id": "../etc"})
    assert response.status_code == 422
    _error_shape(response.json())


def test_x_run_id_is_echoed_and_names_the_trace_file(client, tmp_path):
    response = client.post("/run", json=_body(EXAMPLES[0]), headers={"X-Run-Id": "judge-run-1"})
    assert response.status_code == 200
    assert response.headers["X-Run-Id"] == "judge-run-1"
    assert response.json()["run_id"] == "judge-run-1"
    trace = tmp_path / "trace-judge-run-1.jsonl"
    lines = [json.loads(x) for x in trace.read_text().splitlines()]
    assert lines and all(line["run_id"] == "judge-run-1" for line in lines)
    assert lines[0]["agent"] == "api" and lines[0]["action"] == "run_accepted"
    assert lines[-1]["action"] == "run_finished"
    assert {line["mode"] for line in lines} == {"sample"}
    again = client.post("/run", json=_body(EXAMPLES[0]), headers={"X-Run-Id": "judge-run-1"})
    assert again.status_code == 409
    _error_shape(again.json())


def test_orchestrator_failure_is_a_structured_500(client, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(runs.orchestrator, "run_investigation", boom)
    response = client.post("/run", json=_body(EXAMPLES[0]), headers={"X-Run-Id": "crash-1"})
    assert response.status_code == 500
    body = response.json()
    _error_shape(body)
    assert body["run_id"] == "crash-1" and body["error"]["type"] == "RuntimeError"
    assert body["error"]["recoverable"] is False
    lines = [json.loads(x) for x in (tmp_path / "trace-crash-1.jsonl").read_text().splitlines()]
    assert lines[-1]["action"] == "run_failed" and lines[-1]["status"] == "error"


def test_run_that_outlives_the_budget_is_a_structured_504(client, monkeypatch):
    release = threading.Event()

    def slow(*args, **kwargs):
        release.wait(5)
        raise RuntimeError("released")

    monkeypatch.setattr(runs.orchestrator, "run_investigation", slow)
    monkeypatch.setattr(app_module, "_wait_budget", lambda: 0.2)
    response = client.post("/run", json=_body(EXAMPLES[0]))
    release.set()
    assert response.status_code == 504
    body = response.json()
    _error_shape(body)
    assert body["error"]["recoverable"] is True and body["run_id"]


def test_async_run_lifecycle(client, monkeypatch):
    gate = threading.Event()
    real = runs.orchestrator.run_investigation

    def held(*args, **kwargs):
        gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(runs.orchestrator, "run_investigation", held)
    started = client.post("/runs", json=_body(EXAMPLES[1]), headers={"X-Run-Id": "async-1"})
    assert started.status_code == 202
    assert started.json()["run_id"] == "async-1" and started.headers["X-Run-Id"] == "async-1"
    running = client.get("/runs/async-1").json()
    assert running["status"] == "running" and running["result"] is None
    gate.set()
    assert app_module.registry.get("async-1").wait(30)
    done = client.get("/runs/async-1").json()
    assert done["status"] == "done" and done["error"] is None
    assert RunResult.model_validate(done["result"]).run_id == "async-1"
    assert done["events"] > 3
    missing = client.get("/runs/nope")
    assert missing.status_code == 404
    _error_shape(missing.json())


def _parse_sse(text: str) -> list[tuple[str, str]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if fields:
            events.append((fields.get("event", "message"), fields["data"]))
    return events


def test_sse_streams_trace_lines_then_end(client, tmp_path):
    started = client.post("/runs", json=_body(EXAMPLES[0]), headers={"X-Run-Id": "sse-1"})
    assert started.status_code == 202
    with client.stream("GET", "/runs/sse-1/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    events = _parse_sse(text)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "end" and kinds.count("end") == 1
    traces = [json.loads(d) for k, d in events if k == "trace"]
    assert len(traces) >= 5
    assert all(t["run_id"] == "sse-1" for t in traces)
    assert traces[0]["action"] == "run_accepted" and traces[-1]["action"] == "run_finished"
    end = json.loads(events[-1][1])
    assert end["status"] == "done" and end["end_state"]
    file_lines = (tmp_path / "trace-sse-1.jsonl").read_text().splitlines()
    assert [json.dumps(t) for t in traces] == [json.dumps(json.loads(x)) for x in file_lines]
    # A finished run replays from the start.
    with client.stream("GET", "/runs/sse-1/events") as replay:
        assert len(_parse_sse("".join(replay.iter_text()))) == len(events)


def test_registry_cap_evicts_the_oldest_finished_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    fake = RunResult(run_id="x", status="success")
    registry = RunRegistry(max_runs=3, max_concurrent=2, runner=lambda req, **kw: fake.model_copy())
    req = runs.RunRequest.model_validate(_body(EXAMPLES[0]))
    for i in range(5):
        record = registry.start(req, run_id=f"r{i}")
        assert record.wait(5)
    assert len(registry) == 3
    assert registry.get("r0") is None and registry.get("r1") is None
    assert all(registry.get(f"r{i}") for i in (2, 3, 4))


def test_registry_refuses_beyond_max_concurrent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path))
    gate = threading.Event()

    def held(req, **kw):
        gate.wait(5)
        return RunResult(run_id=kw["run_id"], status="success")

    registry = RunRegistry(max_runs=5, max_concurrent=1, runner=held)
    req = runs.RunRequest.model_validate(_body(EXAMPLES[0]))
    first = registry.start(req, run_id="a")
    with pytest.raises(runs.RegistryBusy):
        registry.start(req, run_id="b")
    gate.set()
    assert first.wait(5)
    assert registry.start(req, run_id="b").wait(5)


def test_per_run_line_cap_counts_dropped_lines():
    record = runs.RunRecord(run_id="cap", max_lines=2)
    for i in range(5):
        record.append(f'{{"i": {i}}}')
    assert len(record.lines) == 2 and record.dropped_lines == 3
