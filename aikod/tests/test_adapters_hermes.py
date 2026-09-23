import pytest
from unittest.mock import patch, MagicMock
from aikod.adapters.hermes import HermesAdapter


def test_manifest_has_models():
    a = HermesAdapter()
    assert len(a.manifest.models) >= 1
    assert all(m.provider_id == "hermes" for m in a.manifest.models)
    assert a.manifest.locality == "server"


@patch("aikod.adapters.hermes.subprocess.run")
def test_health_ok(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="hermes 0.x")
    a = HermesAdapter()
    h = a.health()
    assert h["ok"] is True
    assert "0.x" in h["version"]


@patch("aikod.adapters.hermes.subprocess.run")
def test_health_fails(mock_run):
    mock_run.side_effect = FileNotFoundError
    a = HermesAdapter()
    h = a.health()
    assert h["ok"] is False


@patch("aikod.adapters.hermes.subprocess.run")
def test_spawn_uses_tmux_without_model_flag(mock_run, tmp_path, monkeypatch):
    """Server hermes owns its model config — spawn must NOT pass -m."""
    monkeypatch.setenv("HOME", str(tmp_path))
    a = HermesAdapter()
    bundle = tmp_path / "bundle.md"
    bundle.write_text("context here")
    task = {"id": "sess-1", "spec": "do thing", "model": "gpt-5.6-luna"}
    sid = a.spawn(task, str(bundle))
    assert sid == "sess-1"
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "tmux new-session" in cmd
    assert "aiko-sess-1" in cmd
    assert "chat -q" in cmd
    assert "-m" not in cmd  # model comes from server hermes config


@patch("aikod.adapters.hermes.subprocess.run")
def test_status(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    a = HermesAdapter()
    assert a.status("sess-1")["alive"] is True
    mock_run.return_value = MagicMock(returncode=1)
    assert a.status("sess-1")["alive"] is False
