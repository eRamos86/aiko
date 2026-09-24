"""Tool selection: triviality gate + capability×availability ranking."""
import pytest


@pytest.fixture
def caps_env(tmp_path, monkeypatch):
    import aiko.selection as sel
    monkeypatch.setattr(sel, "CAPS_PATH", tmp_path / "tool_capabilities.yaml")
    sel.load_caps()  # write defaults
    return sel


def test_haiku_is_trivial(caps_env):
    sel = caps_env
    r = sel.recommend("Write a single haiku about server rooms humming at night.")
    assert r["action"] == "answer_self"
    assert r["reasoning_level"] == "low"
    assert r["classification"]["trivial"] is True


def test_chat_is_answered_self(caps_env):
    sel = caps_env
    r = sel.recommend("hey aiko, whats your favorite color?")
    assert r["action"] == "answer_self"


def test_project_task_dispatches(caps_env):
    sel = caps_env
    r = sel.recommend("Theres a login bug in Flux — investigate the auth flow "
                      "in the repo and fix it")
    assert r["action"] == "dispatch"
    assert r["tool"] != "self"


def test_ranking_respects_availability(tmp_path, monkeypatch):
    import aiko.selection as sel
    monkeypatch.setattr(sel, "CAPS_PATH", tmp_path / "caps.yaml")

    def fake_avail(tool):
        return {"agy": 0.0, "codex": 1.0, "hermes": 0.8}.get(tool, 1.0)
    monkeypatch.setattr(sel, "availability", fake_avail)
    rows = sel.rank_tools("plan")
    tools = [r["tool"] for r in rows]
    assert "agy" not in tools or tools.index("agy") > tools.index("codex")
    top = rows[0]["tool"]
    assert top in ("codex", "hermes", "self")


def test_classification_flags():
    from aiko.selection import classify
    c = classify("Research the latest Next.js session cookie patterns and compare")
    assert c["needs_search"] is True
    c = classify("Fix the bug in src/auth.py")
    assert c["needs_code"] is True


def test_caps_file_is_user_tunable(tmp_path, monkeypatch):
    import aiko.selection as sel
    monkeypatch.setattr(sel, "CAPS_PATH", tmp_path / "caps.yaml")
    import yaml
    caps = sel.DEFAULT_CAPS
    caps["agy"]["plan"] = 0.3   # user demotes agy planning
    (tmp_path / "caps.yaml").write_text(yaml.dump({"tools": caps}))
    rows = sel.rank_tools("plan")
    agy_row = [r for r in rows if r["tool"] == "agy"]
    codex_row = [r for r in rows if r["tool"] == "codex"]
    assert agy_row[0]["score"] < codex_row[0]["score"]
