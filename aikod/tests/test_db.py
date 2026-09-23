import pytest
from pathlib import Path
from aikod.db import connect, hash_token


def test_schema_creates_tables(tmp_path):
    c = connect(tmp_path / "aikod.db")
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in {"goal", "task", "session", "event", "provider",
                     "routing_decision", "device_token", "approval"}:
        assert expected in tables


def test_wal_mode(tmp_path):
    c = connect(tmp_path / "aikod.db")
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_round_trip_goal_and_task(tmp_path):
    c = connect(tmp_path / "aikod.db")
    c.execute("INSERT INTO goal VALUES (?,?,?,?,?,?)",
              ("g1", "do thing", "2026-09-21T00:00:00Z", "queued", "dev1", "auto"))
    c.execute("INSERT INTO task(id, goal_id, title, spec, state, fingerprint) VALUES (?,?,?,?,?,?)",
              ("t1", "g1", "title", "spec", "queued", "fp1"))
    goal = c.execute("SELECT * FROM goal WHERE id='g1'").fetchone()
    task = c.execute("SELECT * FROM task WHERE id='t1'").fetchone()
    assert goal[1] == "do thing"
    assert task[1] == "g1"


def test_token_hash_is_deterministic():
    assert hash_token("x") == hash_token("x")
    assert hash_token("x") != hash_token("y")
