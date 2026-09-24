"""The feedback loop: outcomes update rankings, no token spend."""
import json
import time

import pytest


@pytest.fixture
def fb_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AIKO_FEEDBACK_LEDGER", str(tmp_path / "feedback.jsonl"))
    return tmp_path


def test_no_opinion_until_min_attempts(fb_env):
    from aiko.feedback import modifier_for, record_outcome
    record_outcome("codex", "implement", True)
    record_outcome("codex", "implement", True)
    assert modifier_for("codex", "implement") == 0.0   # 2 < MIN_ATTEMPTS
    record_outcome("codex", "implement", True)
    assert modifier_for("codex", "implement") > 0.0    # 3/3 wins → promoted


def test_failures_demote(fb_env):
    from aiko.feedback import modifier_for, record_outcome
    for _ in range(4):
        record_outcome("agy", "plan", False)
    m = modifier_for("agy", "plan")
    assert m <= -0.2                                     # flaky → demoted


def test_modifier_clamped(fb_env):
    from aiko.feedback import modifier_for, record_outcome
    for _ in range(10):
        record_outcome("codex", "implement", False)
    assert modifier_for("codex", "implement") == -0.25   # floor at rate=0
    for _ in range(50):
        record_outcome("hermes", "research", True)
    assert modifier_for("hermes", "research") == 0.15    # never above


def test_shapes_are_independent(fb_env):
    from aiko.feedback import modifier_for, record_outcome
    for _ in range(3):
        record_outcome("codex", "implement", True)
    # codex great at implement but no data for plan → plan unaffected
    assert modifier_for("codex", "implement") > 0.0
    assert modifier_for("codex", "plan") == 0.0


def test_rank_tools_uses_feedback(fb_env, tmp_path, monkeypatch):
    import aiko.selection as sel
    monkeypatch.setattr(sel, "CAPS_PATH", tmp_path / "caps.yaml")
    sel.load_caps()
    from aiko.feedback import record_outcome
    # agy has the best planning floor (0.95) but keeps failing
    for _ in range(5):
        record_outcome("agy", "plan", False)
    rows = sel.rank_tools("plan")
    agy = next(r for r in rows if r["tool"] == "agy")
    codex = next(r for r in rows if r["tool"] == "codex")
    assert agy["feedback"] < 0
    assert agy["score"] < codex["score"]   # learned: agy demoted below codex


def test_daemon_learning_writes_observed(tmp_path, monkeypatch):
    monkeypatch.setenv("AIKO_FEEDBACK_LEDGER", str(tmp_path / "fb.jsonl"))
    import json as _json
    obs = tmp_path / "observed.json"
    monkeypatch.setattr("aikod.router.OBSERVED_PATH", obs)

    from aikod.db import connect
    from aikod.learning import learn_observed
    db = tmp_path / "aikod.db"
    conn = connect(db)
    conn.execute("INSERT INTO goal VALUES ('g','x','2026-01-01','queued','a@b','auto')")
    conn.execute(
        "INSERT INTO task(id, goal_id, title, spec, state, depth, fingerprint, "
        "assigned_provider, assigned_model) "
        "VALUES ('t1','g','fix the login bug in auth.py','fix it','completed',"
        "0,'fp','codex','gpt-5.5')")
    for i, reply in enumerate([
        "done, all tests pass", "done", "fixed the bug",
        "hit the tool-round limit, nya…", "failed to complete",
    ]):
        delta = learn_observed(db, "t1", reply, observed_path=obs)
        assert delta is not None
    data = _json.loads(obs.read_text())
    mod = (data["providers"]["codex"]["models"]["gpt-5.5"]
           ["modifier"].get("debug"))
    # 3 wins / 5 → rate 0.6 → +0.05
    assert mod == 0.05
