import json
import re

import pytest

from tiresias_agent.trace import NullTracer, Tracer, new_run_id


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_emit_writes_jsonl_file_and_stdout(tmp_path, capsys):
    tracer = Tracer("run-1", parent_run_id="run-0", logs_dir=tmp_path)
    tracer.emit(agent="analyst", action="query", sql="SELECT 1", rows=1)
    tracer.emit(agent="critic", action="label", status="rejected", label="background")

    lines = _lines(tmp_path / "trace-run-1.jsonl")
    assert len(lines) == 2
    first = lines[0]
    assert first["service"] == "agent"
    assert first["run_id"] == "run-1"
    assert first["parent_run_id"] == "run-0"
    assert (first["agent"], first["action"], first["status"]) == ("analyst", "query", "success")
    assert first["sql"] == "SELECT 1" and first["rows"] == 1
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", first["timestamp"])
    assert lines[1]["status"] == "rejected"

    stdout = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert stdout == lines


def test_parent_run_id_omitted_when_absent(tmp_path):
    Tracer("run-1", logs_dir=tmp_path).emit(agent="framer", action="frame")
    assert "parent_run_id" not in _lines(tmp_path / "trace-run-1.jsonl")[0]


def test_fields_cannot_override_core_keys(tmp_path):
    Tracer("run-1", logs_dir=tmp_path).emit(
        agent="framer", action="frame", run_id="evil", service="x", timestamp="t"
    )
    line = _lines(tmp_path / "trace-run-1.jsonl")[0]
    assert (line["run_id"], line["service"]) == ("run-1", "agent")
    assert line["timestamp"] != "t"


@pytest.mark.parametrize("run_id", [None, "", "../escape", "a/b", "has space"])
def test_unsafe_or_missing_run_id_writes_stdout_only(tmp_path, capsys, run_id):
    Tracer(run_id, logs_dir=tmp_path).emit(agent="framer", action="frame")
    assert list(tmp_path.iterdir()) == []
    assert json.loads(capsys.readouterr().out)["action"] == "frame"


def test_logs_dir_defaults_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "env-logs"))
    Tracer("run-env").emit(agent="framer", action="frame")
    assert (tmp_path / "env-logs" / "trace-run-env.jsonl").exists()


def test_invalid_status_is_refused(tmp_path):
    with pytest.raises(ValueError):
        Tracer("run-1", logs_dir=tmp_path).emit(agent="a", action="b", status="ok")


def test_write_failure_does_not_raise(tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    Tracer("run-1", logs_dir=blocker).emit(agent="framer", action="frame")
    captured = capsys.readouterr()
    assert json.loads(captured.out)["action"] == "frame"
    assert json.loads(captured.err)["event"] == "trace_write_failed"


def test_secret_looking_fields_are_redacted(tmp_path, capsys):
    secret = "sk-not-a-real-value-123"
    Tracer("run-1", logs_dir=tmp_path).emit(
        agent="nightshift",
        action="chat",
        api_key=secret,
        Authorization=f"Bearer {secret}",
        refresh_TOKEN=secret,
        db_password=secret,
        gate={"headers": {"X-Api-Key": secret, "meterless-app": "tiresias"}},
        note=f"bearer {secret}",
        tokens=1234,
        model="meterless/deep",
    )
    raw = (tmp_path / "trace-run-1.jsonl").read_text() + capsys.readouterr().out
    assert secret not in raw
    line = _lines(tmp_path / "trace-run-1.jsonl")[0]
    for key in ("api_key", "Authorization", "refresh_TOKEN", "db_password", "note"):
        assert line[key] == "***"
    assert line["gate"]["headers"] == {"X-Api-Key": "***", "meterless-app": "tiresias"}
    assert line["tokens"] == 1234
    assert line["model"] == "meterless/deep"


def test_embedded_bearer_credentials_are_redacted(tmp_path, capsys):
    secret = "sk-SECRET-123"
    Tracer("run-1", logs_dir=tmp_path).emit(
        agent="nightshift",
        action="chat",
        note=f"prefix Bearer {secret} suffix",
        nested=[f"curl -H 'authorization: bearer {secret}'"],
    )
    raw = (tmp_path / "trace-run-1.jsonl").read_text() + capsys.readouterr().out
    assert secret not in raw
    line = _lines(tmp_path / "trace-run-1.jsonl")[0]
    assert line["note"] == "prefix Bearer *** suffix"


def test_span_records_duration_and_extra_fields(tmp_path):
    tracer = Tracer("run-1", logs_dir=tmp_path)
    with tracer.span(agent="analyst", action="query", sql="SELECT 1") as extra:
        extra["rows"] = 3
    line = _lines(tmp_path / "trace-run-1.jsonl")[0]
    assert line["status"] == "success"
    assert isinstance(line["duration_ms"], int) and line["duration_ms"] >= 0
    assert (line["sql"], line["rows"]) == ("SELECT 1", 3)


def test_span_can_set_status(tmp_path):
    tracer = Tracer("run-1", logs_dir=tmp_path)
    with tracer.span(agent="critic", action="label") as extra:
        extra["status"] = "degraded"
    assert _lines(tmp_path / "trace-run-1.jsonl")[0]["status"] == "degraded"


def test_span_records_error_and_reraises(tmp_path):
    tracer = Tracer("run-1", logs_dir=tmp_path)
    with pytest.raises(TimeoutError), tracer.span(agent="analyst", action="query"):
        raise TimeoutError("zetaris slow")
    lines = _lines(tmp_path / "trace-run-1.jsonl")
    assert len(lines) == 1
    assert lines[0]["status"] == "error"
    assert lines[0]["error_type"] == "TimeoutError"
    assert "duration_ms" in lines[0]


def test_null_tracer_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tracer = NullTracer("run-1")
    tracer.emit(agent="framer", action="frame", api_key="x")
    with tracer.span(agent="analyst", action="query") as extra:
        extra["rows"] = 1
    with pytest.raises(RuntimeError), tracer.span(agent="analyst", action="query"):
        raise RuntimeError("boom")
    assert list(tmp_path.rglob("*")) == []
    assert capsys.readouterr().out == ""


def test_new_run_id_is_sortable_and_filename_safe():
    ids = [new_run_id() for _ in range(20)]
    for run_id in ids:
        assert re.match(r"^run-\d{8}T\d{6}Z-[0-9a-f]{6}$", run_id)
    assert len(set(ids)) == len(ids)
    assert sorted(ids, key=lambda r: r[:20]) == ids


def test_span_measured_duration_wins_over_caller_field(tmp_path):
    tracer = Tracer("run-dur", logs_dir=tmp_path)
    with tracer.span(agent="a", action="ok", duration_ms=999_999) as extra:
        extra["duration_ms"] = 888_888
    with pytest.raises(RuntimeError), tracer.span(agent="a", action="boom", duration_ms=1):
        raise RuntimeError("x")
    lines = [json.loads(x) for x in (tmp_path / "trace-run-dur.jsonl").read_text().splitlines()]
    assert [x["status"] for x in lines] == ["success", "error"]
    assert all(isinstance(x["duration_ms"], int) and x["duration_ms"] < 999_999 for x in lines)
