"""Small tests for the public request contract."""

from fastapi.testclient import TestClient
from sample_backend.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


def test_echo():
    with TestClient(app) as client:
        response = client.post("/api/echo", json={"message": "hello"})
        assert response.status_code == 200
        assert response.json()["message"] == "hello"


def test_echo_rejects_empty_input():
    with TestClient(app) as client:
        assert client.post("/api/echo", json={"message": ""}).status_code == 422


def test_private_shutdown_is_restricted():
    with TestClient(app) as client:
        assert client.post("/internal/shutdown").status_code == 403



def test_cancel_demo_task_and_query_final_state():
    with TestClient(app) as client:
        started = client.post("/api/tasks/demo").json()
        task_id = started["task_id"]

        response = client.post(f"/api/tasks/{task_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

        state = client.get(f"/api/tasks/{task_id}")
        assert state.json()["status"] == "cancelled"


def test_cancel_unknown_task_returns_404():
    with TestClient(app) as client:
        assert client.post("/api/tasks/unknown/cancel").status_code == 404
