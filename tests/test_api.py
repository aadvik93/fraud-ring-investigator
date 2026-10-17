"""API boundary tests that do not require a live FalkorDB connection."""

from fastapi.testclient import TestClient

from app import app


def test_unknown_investigation_mode_is_rejected_before_streaming():
    client = TestClient(app)
    response = client.post("/api/investigate/ALERT_001?mode=unsupported")

    assert response.status_code == 422
    assert "Unsupported mode" in response.json()["detail"]
