from pathlib import Path
from unittest.mock import patch, MagicMock

from aikod.db import connect
from aikod.scheduler import tick, infer_task_type
from aikod.router import RoutingDecision
from aikod.__main__ import spawn_routed_task


class FakeAdapter:
    def __init__(self):
        from aikod.adapters.base import AdapterManifest
        self.manifest = AdapterManifest(
            provider_id="hermes", one_shot=True, resume=False, fork=False,
            structured_output=False, control_daemon="none", concurrency_limit=1,
            quota_model="unmetered", sandbox_tiers=[], health_signals=[],
        )

    def health(self):
        return {"ok": True}

    def spawn(self, task, bundle_path):
        return task["id"]


def _mk_decision():
    return RoutingDecision(
        provider_id="hermes", model="glm-5.2", yaml_score=0.7,
        observed_modifier=0.0, final_score=0.7,
        reasoning="test", candidates=[],
    )


def _mk_decision():
    return RoutingDecision(
        provider_id="hermes", model="glm-5.2", yaml_score=0.7,
        observed_modifier=0.0, final_score=0.7,
        reasoning="test", candidates=[],
    )


def test_infer_task_type():
    assert infer_task_type("fix the login bug") == "debug"
    assert infer_task_type("implement oauth flow") == "implement"
    assert infer_task_type("write documentation for the API") == "write_docs"
    assert infer_task_type("research vector databases") == "research"
    assert infer_task_type("plan the migration") == "plan"


def test_tick_routes_ready_task(tmp_path):
    db = tmp_path / "aikod.db"
    conn = connect(db)
    conn.execute("INSERT INTO goal VALUES (?,?,?,?,?,?)",
                 ("g1", "fix the login bug", "2026-09-22T00:00:00Z", "queued", "dev1", "auto"))
    conn.execute("INSERT INTO task(id, goal_id, title, spec, state, fingerprint) "
                 "VALUES ('t1','g1','fix the login bug','fix the login bug','ready','fp1')")
    with patch("aikod.scheduler.choose", return_value=_mk_decision()):
        result = tick(db, log=False)
    assert result is not None
    task_id, decision = result
    assert task_id == "t1"
    row = conn.execute("SELECT state, assigned_provider, assigned_model FROM task WHERE id='t1'").fetchone()
    assert row[0] == "running"
    assert row[1] == "hermes"
    assert row[2] == "glm-5.2"


def test_tick_no_ready_tasks(tmp_path):
    db = tmp_path / "aikod.db"
    connect(db)  # schema only
    assert tick(db, log=False) is None


def test_spawn_routed_task_creates_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    db = tmp_path / "aikod.db"
    conn = connect(db)
    conn.execute("INSERT INTO goal VALUES (?,?,?,?,?,?)",
                 ("g1", "do thing", "2026-09-22T00:00:00Z", "queued", "dev1", "auto"))
    conn.execute("INSERT INTO task(id, goal_id, title, spec, state, fingerprint) "
                 "VALUES ('t1','g1','do thing','do thing','running','fp1')")

    with patch("aikod.__main__.assemble_bundle", return_value="# bundle\nctx"):
        adapter = FakeAdapter()
        sid = spawn_routed_task(db, {"hermes": adapter}, "t1", _mk_decision())
    assert sid == "t1"
    row = conn.execute("SELECT id, provider_id, state FROM session WHERE task_id='t1'").fetchone()
    assert row == ("t1", "hermes", "live")
