import time
import uuid
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from aikod.api import create_app
from aikod.db import connect

SECRET = "test-nova-secret"


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = create_app(tmp_path / "aikod.db", nova_jwt_secret=SECRET)
    return TestClient(app)


def make_token(payload=None, secret=SECRET):
    body = payload or {"id": 1, "email": "ethan@eramos.us"}
    return pyjwt.encode(body, secret, algorithm="HS256")


def test_health_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "adapters" in body


def test_create_goal_requires_nova_jwt(client):
    r = client.post("/goals", json={"text": "do thing"})
    assert r.status_code in (401, 403)  # missing header


def test_create_goal_with_valid_nova_jwt(client):
    tok = make_token()
    r = client.post("/goals", json={"text": "investigate flux login bug"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["goal_id"].startswith("goal-")
    assert body["task_id"].startswith("task-")

    # goal + task landed in db
    conn = connect(client.app.state.db_path if hasattr(client.app, "state") else None) if False else None
    r2 = client.get(f"/goals/{body['goal_id']}", headers={"Authorization": f"Bearer {tok}"})
    assert r2.status_code == 200
    assert len(r2.json()["tasks"]) == 1


def test_rejects_non_nova_secret(client):
    tok = make_token(secret="wrong-secret")
    r = client.post("/goals", json={"text": "x"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_rejects_token_missing_claims(client):
    tok = make_token(payload={"id": 1})  # no email
    r = client.post("/goals", json={"text": "x"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_expired_token_rejected(client):
    tok = pyjwt.encode({"id": 1, "email": "e@x.us", "exp": int(time.time()) - 3600},
                       SECRET, algorithm="HS256")
    r = client.post("/goals", json={"text": "x"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
