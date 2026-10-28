"""Integrated desktop contract: template-owned runtime exposes the original detector API."""
from fastapi.testclient import TestClient
from sample_backend.main import create_app

def test_wafer_routes_and_authenticated_ipc(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SESSION_TOKEN", "random-session")
    with TestClient(create_app()) as client:
        assert client.get("/api/config/active").status_code == 401
        headers = {"x-session-token": "random-session"}
        response = client.get("/api/config/active", headers=headers)
        assert response.status_code == 200
        config = response.json()
        assert "detector" in config and "calibrations" in config
        status = client.get("/api/monitor/status", headers=headers)
        assert status.status_code == 200
        frame = client.get("/api/monitor/frame", headers=headers)
        assert frame.status_code == 200
        assert "jpeg_base64" in frame.json()
        assert client.get("/api/health", headers=headers).json()["status"] == "ready"

def test_shutdown_cannot_use_session_token(monkeypatch):
    monkeypatch.setenv("APP_SESSION_TOKEN", "same-session")
    monkeypatch.setenv("APP_CONTROL_TOKEN", "different-control")
    with TestClient(create_app()) as client:
        response = client.post("/internal/shutdown", headers={"x-control-token": "same-session"})
        assert response.status_code == 403
