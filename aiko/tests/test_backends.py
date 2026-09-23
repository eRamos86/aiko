import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from aiko.backends import LocalBackend, ServerBackend, AllBackends


def test_local_backend_dispatch_uses_configured_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "agents": [{"name": "codex", "command": "codex",
                    "one_shot": ["exec", "--skip-git-repo-check", "{prompt}"]}],
    })
    with patch("aiko.backends.subprocess.Popen") as mock_popen:
        b = LocalBackend()
        result = b.dispatch("do a thing")
        assert result["dispatched"] is True
        assert result["agent"] == "codex"
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "codex"
        assert "do a thing" in cmd[-1]


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
    with patch("aiko.backends.subprocess.Popen") as mock_popen:
        b = AllBackends()
        result = b.dispatch("hello")
        assert result["target"] == "local"
        mock_popen.assert_called_once()


def test_all_backends_local_only_mode(monkeypatch):
    """local-only user (no servers key) still gets local target."""
    monkeypatch.setattr("aiko.backends._load_cfg", lambda: {
        "agents": [{"name": "hermes", "command": "hermes", "one_shot": ["{prompt}"]}],
    })
    b = AllBackends()
    assert b.remote is None
    targets = b.list_targets()
    assert targets[0]["target"] == "local"
