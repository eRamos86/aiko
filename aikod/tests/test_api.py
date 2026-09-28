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


def test_approval_inbox_and_single_decision(client, tmp_path):
    token = make_token()
    created = client.post("/goals", json={"text": "deploy aiko"},
                          headers={"Authorization": f"Bearer {token}"}).json()
    db = connect(tmp_path / "aikod.db")
    db.execute("INSERT INTO approval VALUES (?,?,?,?,?,?,?,?)",
               ("approval-1", created["task_id"], "deploy", "abc123", "2026-09-28T00:00:00Z",
                None, None, None))
    inbox = client.get("/approvals", headers={"Authorization": f"Bearer {token}"})
    assert inbox.status_code == 200
    assert inbox.json()["approvals"] == [{
        "id": "approval-1", "task_id": created["task_id"], "action": "deploy",
        "payload_digest": "abc123", "requested_at": "2026-09-28T00:00:00Z",
        "decided_at": None, "decision": None, "title": "deploy aiko",
    }]
    decided = client.post("/approvals/approval-1/decision", json={"decision": "granted"},
                          headers={"Authorization": f"Bearer {token}"})
    assert decided.status_code == 200
    assert decided.json()["decision"] == "granted"
    assert client.post("/approvals/approval-1/decision", json={"decision": "denied"},
                       headers={"Authorization": f"Bearer {token}"}).status_code == 409
