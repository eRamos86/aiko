from pathlib import Path
from unittest.mock import patch

from aiko.backends import LocalBackend, ServerBackend, AllBackends


def test_local_backend_dispatch_uses_configured_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "agents": [{"name": "codex", "command": "codex",
                    "one_shot": ["exec", "--skip-git-repo-check", "{prompt}"]}],
    })
    with patch("aiko.backends.SessionManager.dispatch", return_value={
            "id": "local-test", "agent": "codex", "target": "local"}) as dispatch:
        b = LocalBackend()
        result = b.dispatch("do a thing")
        assert result["dispatched"] is True
        assert result["agent"] == "codex"
        dispatch.assert_called_once_with("codex", Path.cwd(), "do a thing",
                                         target="local", repo_direct=False)
        assert result["task_id"] == "local-test"


def test_local_backend_lists_targets(tmp_path, monkeypatch):
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "agents": [{"name": "hermes", "command": "hermes", "one_shot": ["chat", "-q", "{prompt}"]}],
    })
    b = LocalBackend()
    targets = b.list_targets()
    assert targets == [{"target": "local", "agents": ["hermes"]}]


def test_server_backend_unknown_target_raises(monkeypatch):
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "servers": [{"name": "poop", "url": "https://x", "auth": "none"}],
    })
    b = ServerBackend()
    import pytest
    with pytest.raises(RuntimeError, match="unknown server"):
        b.dispatch("x", target="nonexistent")


def test_all_backends_local_dispatch_without_servers(monkeypatch):
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "agents": [{"name": "codex", "command": "codex", "one_shot": ["{prompt}"]}],
    })
    with patch("aiko.backends.SessionManager.dispatch", return_value={
            "id": "local-test", "agent": "codex", "target": "local"}) as dispatch:
        b = AllBackends()
        result = b.dispatch("hello")
        assert result["target"] == "local"
        dispatch.assert_called_once()


def test_all_backends_local_only_mode(monkeypatch):
    """local-only user (no servers key) still gets local target."""
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "agents": [{"name": "hermes", "command": "hermes", "one_shot": ["{prompt}"]}],
    })
    b = AllBackends()
    assert b.remote is None
    targets = b.list_targets()
    assert targets[0]["target"] == "local"


def test_backend_reconstructs_failed_task_and_records_outcome_once(monkeypatch):
    cfg = {"agents": [{"name": "codex", "command": "codex"}]}
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: cfg)
    backend = LocalBackend()
    backend.sessions._save({"id": "local-test", "agent": "codex", "target": "local",
                            "state": "failed", "session_state": "exited", "exit_code": 7,
                            "title": "test"})
    with patch("aiko.feedback.record_outcome") as record:
        restored = LocalBackend()
        assert restored.task_status("local-test")["state"] == "failed"
        assert LocalBackend().task_status("local-test")["exit_code"] == 7
        record.assert_called_once_with("codex", "implement", False)
    assert restored.list_sessions()[0]["id"] == "local-test"
    assert restored.session_story("local-test")["goal"]["status"] == "failed"
    assert restored.task_status("missing") == {"error": "unknown task"}


def test_backend_returns_dispatch_guard_error_and_preserves_cwd(tmp_path):
    backend = LocalBackend({"agents": [{"name": "codex", "command": "codex"}]})
    with patch.object(backend.sessions, "dispatch", side_effect=ValueError("protected branch")) as dispatch:
        result = backend.dispatch("work", cwd=tmp_path)
        assert result == {"error": "protected branch"}
        dispatch.assert_called_once_with("codex", tmp_path, "work", target="local", repo_direct=False)
