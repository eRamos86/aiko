"""Subagent spawning (ADR-018)."""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AIKO_FEEDBACK_LEDGER", str(tmp_path / "fb.jsonl"))
    from aiko import subagents
    monkeypatch.setattr(subagents, "REGISTRY", tmp_path / "registry.yaml")
    return subagents, tmp_path


def _write_registry(tmp_path, agents):
    import yaml
    (tmp_path / "registry.yaml").write_text(yaml.safe_dump({"subagents": agents}))


class _FakeBrain:
    name = "fake-brain"
    def complete(self, messages, tools=None):
        return {"content": f"[{len(messages)} msgs] subagent answer"}


class _FakeBackend:
    def dispatch(self, text, target=None):
        return {"task_id": "task-xyz", "target": target or "codex"}


class _Caller:
    brain = _FakeBrain()
    backend = _FakeBackend()


def test_load_subagent(env):
    subagents, tmp_path = env
    _write_registry(tmp_path, [{"name": "planner", "prompt": "Plan things."}])
    spec = subagents.load_subagent("planner")
    assert spec["prompt"] == "Plan things."


def test_load_unknown_errors(env):
    subagents, tmp_path = env
    _write_registry(tmp_path, [{"name": "planner"}])
    with pytest.raises(ValueError, match="unknown subagent"):
        subagents.load_subagent("wizard")


def test_light_subagent_runs_on_own_brain(env):
    subagents, tmp_path = env
    _write_registry(tmp_path, [{"name": "critic", "prompt": "Critique."}])
    result = subagents.spawn(_Caller(), subagents.load_subagent("critic"), "review this")
    assert result["host"] == "self"
    assert "subagent answer" in result["result"]
    assert result["depth"] == 1
    assert result["duration_s"] >= 0


def test_heavy_subagent_dispatches(env):
    subagents, tmp_path = env
    _write_registry(tmp_path, [{"name": "codewriter", "prompt": "Write code."}])
    result = subagents.spawn(_Caller(), subagents.load_subagent("codewriter"), "build X")
    assert result.get("task_id") == "task-xyz"


def test_depth_marker_increments(env):
    subagents, tmp_path = env
    _write_registry(tmp_path, [{"name": "planner"}])
    result = subagents.spawn(_Caller(), subagents.load_subagent("planner"),
                             "plan [spawn-depth:3]")
    assert result["depth"] == 4


def test_outcome_recorded(env):
    subagents, tmp_path = env
    _write_registry(tmp_path, [{"name": "critic"}])
    subagents.spawn(_Caller(), subagents.load_subagent("critic"), "critique me")
    import json
    lines = (tmp_path / "fb.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["tool"] == "subagent:critic"
    assert rec["success"] is True
