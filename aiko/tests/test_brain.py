from unittest.mock import patch, MagicMock

import pytest

from aiko.brain import Brain, list_brains


CFG = {
    "brains": [
        {"name": "test-nim", "type": "openai_compatible",
         "base_url": "https://api.test/v1", "api_key": "sk-test",
         "model": "model-a"},
        {"name": "test-nim-2", "type": "openai_compatible",
         "base_url": "https://api.test/v1", "api_key": "sk-test",
         "model": "model-b"},
        {"name": "test-ollama", "type": "ollama",
         "endpoint": "http://localhost:11434", "model": "llama"},
    ],
    "default_brain": "test-nim-2",
}


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr("aiko.brain._load_cfg", lambda: CFG)


def test_list_brains(cfg):
    names = [b["name"] for b in list_brains()]
    assert names == ["test-nim", "test-nim-2", "test-ollama"]


def test_default_brain_selected(cfg):
    assert Brain().name == "test-nim-2"


def test_named_brain(cfg):
    assert Brain(name="test-ollama").model == "llama"


def test_missing_brain_raises(cfg):
    with pytest.raises(RuntimeError, match="not found"):
        Brain(name="nope")


def test_openai_completion_parses_tool_calls(cfg):
    with patch("aiko.brain.httpx.post") as mock_post:
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"choices": [{"message": {
            "content": "hi",
            "tool_calls": [{"function": {"name": "dispatch_task",
                                          "arguments": '{"text": "x"}'}}],
        }}]}
        mock_post.return_value = resp
        b = Brain(name="test-nim")
        result = b.complete([{"role": "user", "content": "hi"}])
        assert result["content"] == "hi"
        assert result["tool_calls"][0]["name"] == "dispatch_task"
        assert result["tool_calls"][0]["arguments"] == {"text": "x"}


def test_503_falls_back_to_sibling_model(cfg):
    with patch("aiko.brain.httpx.post") as mock_post:
        overloaded = MagicMock(status_code=503)
        ok = MagicMock(status_code=200)
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"choices": [{"message": {"content": "from model-b"}}]}
        mock_post.side_effect = [overloaded, ok]
        b = Brain(name="test-nim")  # model-a; sibling = model-b
        result = b.complete([{"role": "user", "content": "hi"}])
        assert result["content"] == "from model-b"
        assert mock_post.call_count == 2


def test_no_brains_configured(monkeypatch):
    monkeypatch.setattr("aiko.brain._load_cfg", lambda: {})
    with pytest.raises(RuntimeError, match="No brains configured"):
        Brain()
