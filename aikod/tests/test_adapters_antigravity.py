import pytest
from unittest.mock import patch, MagicMock
from aikod.adapters.antigravity import AGYAdapter


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AGY_BRIDGE_URL", "http://test:4091")
    monkeypatch.setenv("AGY_BRIDGE_TOKEN", "tok")


def test_manifest_locality_is_client(env):
    a = AGYAdapter()
    assert a.manifest.locality == "client"
    assert any(m.name == "gemini-3-pro" for m in a.manifest.models)


@patch("aikod.adapters.antigravity.requests.get")
def test_health_ok(mock_get, env):
    mock_get.return_value = MagicMock(ok=True, json=lambda: {"agy_version": "x"})
    a = AGYAdapter()
    assert a.health()["ok"] is True


@patch("aikod.adapters.antigravity.requests.get")
def test_health_unreachable(mock_get, env):
    mock_get.side_effect = ConnectionError("nope")
    a = AGYAdapter()
    h = a.health()
    assert h["ok"] is False
    assert "nope" in h["error"]


@patch("aikod.adapters.antigravity.requests.post")
def test_spawn(mock_post, env, tmp_path):
    bundle = tmp_path / "b.md"
    bundle.write_text("ctx")
    mock_post.return_value = MagicMock(ok=True, json=lambda: {"session_id": "s1"},
                                        raise_for_status=lambda: None)
    a = AGYAdapter()
    sid = a.spawn({"id": "s1", "spec": "do"}, str(bundle))
    assert sid == "s1"
    _, kwargs = mock_post.call_args
    assert "ctx" in kwargs["json"]["spec"]
