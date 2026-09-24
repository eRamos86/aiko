"""Streaming: brain SSE parsing + orchestrator step_stream event flow."""
import json

from aiko.brain import _StreamState

# assembled from parts so tag literals survive any transport mangling
OPEN = "<" + "think>"
CLOSE = "<" + "/think>"


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def test_stream_reasoning_field():
    s = _StreamState()
    evt = s.feed_sse(_sse({"reasoning_content": "hmm"}))
    assert evt == {"type": "thinking", "delta": "hmm"}
    assert s.thinking == "hmm"


def test_stream_text_delta():
    s = _StreamState()
    evt = s.feed_sse(_sse({"content": "nya~"}))
    assert evt == {"type": "text", "delta": "nya~"}
    assert s.text == "nya~"


def test_stream_think_tags():
    s = _StreamState()
    s.feed_sse(_sse({"content": "visible" + OPEN}))
    s.feed_sse(_sse({"content": "secret reasoning"}))
    s.feed_sse(_sse({"content": CLOSE + "more text"}))
    assert s.thinking == "secret reasoning"
    assert s.text == "visiblemore text"


def test_stream_tool_fragments():
    s = _StreamState()
    s.feed_sse(_sse({"tool_calls": [
        {"index": 0, "function": {"name": "dispatch_task",
                                  "arguments": "{\"te"}}]}))
    s.feed_sse(_sse({"tool_calls": [
        {"index": 0, "function": {"arguments": "xt\": 1}"}}]}))
    final = s.final()
    assert final["tool_calls"] == [{"name": "dispatch_task",
                                    "arguments": {"text": 1}}]


def test_stream_ignores_noise():
    s = _StreamState()
    assert s.feed_sse("") is None
    assert s.feed_sse("event: ping") is None
    assert s.feed_sse("data: [DONE]") is None
    assert s.feed_sse("data: {\"choices\": []}") is None
    assert s.feed_sse("data: not json") is None


def test_step_stream_tool_round(monkeypatch):
    from aiko import agent as agent_mod
    monkeypatch.setattr(agent_mod, "_tool_defs", lambda: [])  # keep hermetic: no MCP
    from aiko.agent import Orchestrator

    class FakeBrain:
        def __init__(self):
            self.n = 0

        def complete_stream(self, messages, tools=None):
            self.n += 1
            if self.n == 1:
                yield {"type": "thinking", "delta": "hmm"}
                yield {"type": "final", "content": "",
                        "tool_calls": [{"name": "list_targets", "arguments": {}}]}
            else:
                yield {"type": "text", "delta": "done"}
                yield {"type": "final", "content": "done nya", "tool_calls": []}

    o = Orchestrator.__new__(Orchestrator)
    o.brain = FakeBrain()
    o.history = []
    o._execute = lambda name, args: '{"ok": true}'
    events = list(o.step_stream("hi"))
    kinds = [e["type"] for e in events]
    assert kinds == ["thinking", "tool", "tool_result", "text", "reply"]
    assert events[-1]["content"] == "done nya"
    # history must match step()'s shape: user, assistant+tool_calls, tool, assistant
    roles = [m["role"] for m in o.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
