from fastapi.testclient import TestClient

from tiresias_agent.app import app

client = TestClient(app)


def test_health_reports_service_and_mode():
    body = client.get("/health").json()
    assert body["service"] == "agent"
    assert "sample_mode" in body


def test_branch_and_purge_are_explicitly_not_implemented_yet():
    for path in ("/runs/run-x/branch", "/memory/purge"):
        response = client.post(path, json={})
        assert response.status_code == 501
        body = response.json()
        assert body["status"] == "error" and body["error"]["type"] == "NotImplemented"
