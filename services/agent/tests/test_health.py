from fastapi.testclient import TestClient

from tiresias_agent.app import app

client = TestClient(app)


def test_health_reports_service_and_mode():
    body = client.get("/health").json()
    assert body["service"] == "agent"
    assert "sample_mode" in body


def test_branch_is_explicitly_not_implemented_yet():
    # POST /memory/purge is implemented since feat/memory (tests/test_memory.py).
    response = client.post("/runs/run-x/branch", json={})
    assert response.status_code == 501
    body = response.json()
    assert body["status"] == "error" and body["error"]["type"] == "NotImplemented"
