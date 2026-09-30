import pytest
from fastapi.testclient import TestClient

from app.core.config import reset_settings_cache
from app.database.session import reset_engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/api.sqlite3")
    monkeypatch.setenv("NEXUS_MODE", "simulation")
    reset_settings_cache()
    reset_engine()
    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()
    reset_settings_cache()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_objective_intake_and_run(client):
    created = client.post(
        "/api/objectives", json={"title": "pipeline", "product_categories": ["refurbished_laptop"]}
    )
    assert created.status_code == 200
    assert client.post("/api/run").status_code == 200
    state = client.get("/api/state").json()
    assert state["objectives"][0]["title"] == "pipeline"
    assert state["budget"]["limit_usd"] == 200.0


def test_unknown_category_is_rejected(client):
    response = client.post("/api/objectives", json={"title": "x", "product_categories": ["spaceships"]})
    assert response.status_code == 422


def test_emergency_stop_is_reflected_in_state(client):
    client.post("/api/control", json={"emergency_stop": True})
    assert client.get("/api/state").json()["control"]["emergency_stop"] is True


def test_dashboard_is_served(client):
    body = client.get("/").text
    assert "NEXUS control" in body
