"""Server-side orchestrator (ADR-012): delegation stays on the server."""
import json
from pathlib import Path

import pytest


class _FakeBrain:
    """Two-round brain: reads transcript, then completes."""

    def __init__(self):
        self.n = 0

    def complete(self, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return {"content": "",
                    "tool_calls": [{"name": "read_transcript",
                                    "arguments": {"session_id": "s1"}}]}
        return {"content": "All done — worker finished the research, nya~!",
                "tool_calls": []}


def test_server_orchestrator_completes_task(tmp_path, monkeypatch):
    from aikod import orchestrator as orch_mod

    db = tmp_path / "aikod.db"
    from aikod.db import connect
    conn = connect(db)
    conn.execute("INSERT INTO goal VALUES ('g1','test goal','2026-01-01','queued','a@b','auto')")
    conn.execute(
        "INSERT INTO task(id, goal_id, title, spec, state, depth, fingerprint) "
        "VALUES ('t1','g1','test title','test spec','running',0,'fp1')")
    conn.execute(
        "INSERT INTO session(id, task_id, provider_id, model, state) "
        "VALUES ('s1','t1','hermes','m','exited')")
    # fake transcript file
    tr = tmp_path / "transcripts"
    tr.mkdir()
    (tr / "s1.log").write_text("user: do the thing\nassistant: done, all output here")
    monkeypatch.setattr(orch_mod, "TRANSCRIPTS", tr)

    o = orch_mod.ServerOrchestrator(db, "t1", brain_factory=_FakeBrain)
    reply = o.step("worker exited; review and wrap up")
    assert "done" in reply.lower()
    assert o.brain.n == 2
    # history shape: system, user, assistant+tool_calls, tool, assistant
    roles = [m["role"] for m in o.history]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]


def test_server_orchestrator_dispatches_subtask(tmp_path, monkeypatch):
    from aikod import orchestrator as orch_mod

    db = tmp_path / "aikod.db"
    from aikod.db import connect
    conn = connect(db)
    conn.execute("INSERT INTO goal VALUES ('g1','g','2026-01-01','queued','a@b','auto')")
    conn.execute(
        "INSERT INTO task(id, goal_id, title, spec, state, depth, fingerprint) "
        "VALUES ('t1','g1','fix the login bug','fix it','running',0,'fp2')")

    class SubtaskBrain:
        def __init__(self):
            self.n = 0

        def complete(self, messages, tools=None):
            self.n += 1
            if self.n == 1:
                return {"content": "",
                        "tool_calls": [{"name": "dispatch_subtask",
                                        "arguments": {
                                            "title": "write tests",
                                            "spec": "write tests for the fix"}}]}
            return {"content": "subtask queued", "tool_calls": []}

    o = orch_mod.ServerOrchestrator(db, "t1", brain_factory=SubtaskBrain)
    reply = o.step("worker exited; more work needed")
    assert "subtask" in reply.lower()
    # the subtask must exist in THIS server's DB with parent linkage
    conn2 = connect(db)
    rows = conn2.execute(
        "SELECT id, parent_task_id, state, depth FROM task "
        "WHERE parent_task_id='t1'").fetchall()
    assert len(rows) == 1
    assert rows[0][2] in ("ready", "queued")
    assert rows[0][3] == 1  # depth increments under the parent
