"""PUBLIC_DEMO=true (the hosted demo, deploy/gcp): sample mode only and no memory purge.

A visitor of the public demo must not be able to switch a run or the directory to live mode
(the server has no Zetaris or Nightshift, and must not try) or wipe the memory every visitor
shares. Off by default: locally nothing changes.
"""

import json

import pytest
from fastapi.testclient import TestClient
from loop_helpers import EXAMPLES

from tiresias_agent import app as app_module

client = TestClient(app_module.app)


def _request(sample_mode=None):
    body = json.loads(EXAMPLES[0].read_text())
    body.setdefault("options", {})["sample_mode"] = sample_mode
    body["options"]["memory"] = "cold"  # no cognition call from this test
    return body


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")


def test_health_reports_the_demo_flag(demo):
    assert client.get("/health").json()["public_demo"] is True


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    assert client.get("/health").json()["public_demo"] is False


@pytest.mark.parametrize("path", ["/run", "/runs"])
def test_a_run_asking_for_live_mode_is_refused(demo, path):
    res = client.post(path, json=_request(sample_mode=False))
    assert res.status_code == 403
    assert res.json()["error"]["type"] == "LiveModeDisabled"


def test_a_sample_run_still_works(demo):
    res = client.post("/run", json=_request())
    assert res.status_code == 200
    assert res.json()["sample_mode"] is True


def test_live_directory_is_refused(demo):
    assert client.get("/entities?sample_mode=false").status_code == 403
    assert client.get("/entities/40545/suggestions?sample_mode=false").status_code == 403
    assert client.get("/entities").status_code == 200


def test_live_server_config_is_refused_too(demo, monkeypatch):
    # A demo misconfigured with SAMPLE_MODE=false still never runs live.
    monkeypatch.setattr(app_module, "settings", type("S", (), {"sample_mode": False})())
    assert client.post("/run", json=_request()).status_code == 403


def test_purge_is_refused_and_cognition_is_not_called(demo, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("cognition must not be called")

    monkeypatch.setattr(app_module, "memory_client", boom)
    res = client.post("/memory/purge")
    assert res.status_code == 403
    assert res.json()["error"]["type"] == "PurgeDisabled"
