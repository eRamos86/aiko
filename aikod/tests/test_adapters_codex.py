from unittest.mock import patch, MagicMock
from aikod.adapters.codex import CodexAdapter


def test_manifest():
    a = CodexAdapter()
    assert a.manifest.provider_id == "codex"
    assert a.manifest.locality == "server"
    assert a.manifest.quota_model == "estimated"
    assert any(m.name == "gpt-5.5" for m in a.manifest.models)


@patch("aikod.adapters.codex.subprocess.run")
def test_health(mock_run, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AUTH_PATH", str(tmp_path / "auth.json"))
    (tmp_path / "auth.json").write_text("{}")
    mock_run.return_value = MagicMock(returncode=0, stdout="codex-cli 0.154.0")
    a = CodexAdapter()
    h = a.health()
    assert h["ok"] is True
    assert h["auth_present"] is True


@patch("aikod.adapters.codex.subprocess.run")
def test_spawn_uses_tmux(mock_run, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = CodexAdapter()
    bundle = tmp_path / "bundle.md"
    bundle.write_text("context here")
    task = {"id": "sess-9", "spec": "do thing", "model": "gpt-5.5"}
    sid = a.spawn(task, str(bundle))
    assert sid == "sess-9"
    cmd = mock_run.call_args[0][0]
    assert "tmux new-session" in cmd
    assert "codex exec" in cmd
    assert "gpt-5.5" in cmd
