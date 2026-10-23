"""Shared helpers for the Loop A tests."""

import json
from pathlib import Path

from tiresias_agent.models import RunRequest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((ROOT / "input_examples").glob("*.json"))


class RecordingTracer:
    run_id = "run-test-loop"
    trace_file = None

    def __init__(self):
        self.events = []

    def emit(self, *, agent, action, status="success", **fields):
        self.events.append({"agent": agent, "action": action, "status": status, **fields})


def request(name_prefix: str, **overrides) -> RunRequest:
    path = next(p for p in EXAMPLES if p.name.startswith(name_prefix))
    data = json.loads(path.read_text())
    data.update(overrides)
    return RunRequest.model_validate(data)
