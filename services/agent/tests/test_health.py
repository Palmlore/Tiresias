from fastapi.testclient import TestClient

from tiresias_agent.app import app

client = TestClient(app)


def test_health_reports_service_and_mode():
    body = client.get("/health").json()
    assert body["service"] == "agent"
    assert "sample_mode" in body


def test_run_is_explicitly_not_implemented_yet():
    response = client.post("/run", json={})
    assert response.status_code == 501
    assert response.json()["error"]["type"] == "NotImplemented"
