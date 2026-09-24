"""Aiko Brain — the orchestrator's mind, fully config-driven (ADR: fully open).

Reads `brains:` from ~/.aiko/config.yaml. Two types:
  - openai_compatible: base_url + api_key (NIM, OpenRouter, OpenAI, vLLM, ...)
  - ollama: local endpoint
/model lists these; switching is instant. ${ENV_VAR} expansion on api_key.
"""
import json
import os
import re
from pathlib import Path

import httpx
import yaml


def _load_cfg() -> dict:
    p = Path.home() / ".aiko" / "config.yaml"
    try:
        return yaml.safe_load(p.read_text()) or {}
    except FileNotFoundError:
        return {}


def _expand(value: str) -> str:
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{([A-Z0-9_]+)\}",
                  lambda m: os.environ.get(m.group(1), ""), value)


def list_brains() -> list[dict]:
    """All configured brains, resolved (api keys expanded)."""
    cfg = _load_cfg()
    brains = []
    for b in cfg.get("brains", []):
        entry = dict(b)
        if entry.get("api_key"):
            entry["api_key"] = _expand(entry["api_key"])
        brains.append(entry)
    return brains


class Brain:
    """Chat completions with history + tools. Provider-agnostic."""

    def __init__(self, name: str | None = None, reasoning: str = "medium"):
        cfg = _load_cfg()
        brains = cfg.get("brains", [])
        if not brains:
            raise RuntimeError("No brains configured — add one to ~/.aiko/config.yaml, nya~")
        if name:
            match = [b for b in brains if b.get("name") == name]
            if not match:
                available = ", ".join(b.get("name", "?") for b in brains)
                raise RuntimeError(f"Brain '{name}' not found. Available: {available}")
            brain = match[0]
        else:
            default = cfg.get("default_brain")
            brain = next((b for b in brains if b.get("name") == default), brains[0])

        self.name = brain.get("name", "unnamed")
        self.type = brain.get("type", "openai_compatible")
        self.model = brain.get("model", "")
        self.base_url = brain.get("base_url", "").rstrip("/")
        self.endpoint = brain.get("endpoint", "http://localhost:11434").rstrip("/")
        self.api_key = _expand(brain.get("api_key", ""))
        self.reasoning = reasoning  # low | medium | high — hint for providers

        if self.type == "openai_compatible" and not self.api_key:
            raise RuntimeError(f"Brain '{self.name}' has no api_key (or its ${{ENV}} is unset)")

    def describe(self) -> str:
        if self.type == "ollama":
            return f"{self.name} (ollama:{self.model})"
        return f"{self.name} ({self.model})"

    def set_reasoning(self, level: str) -> None:
        self.reasoning = level

    # ── fallback chain within same provider type ─────────────────
    def _sibling_models(self) -> list[str]:
        """Other models from brains sharing my type+base_url (503 resilience)."""
        siblings = []
        for b in list_brains():
            if (b.get("type") == self.type
                    and b.get("base_url", "").rstrip("/") == self.base_url
                    and b.get("model") not in siblings
                    and b.get("model") != self.model):
                siblings.append(b.get("model"))
        return siblings

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One turn. Returns {"content": str, "tool_calls": [...]}."""
        if self.type == "ollama":
            return self._complete_ollama(messages, tools)
        return self._complete_openai(messages, tools)

    def _complete_ollama(self, messages, tools) -> dict:
        body = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            body["tools"] = tools
        r = httpx.post(f"{self.endpoint}/api/chat", json=body, timeout=180)
        r.raise_for_status()
        data = r.json()
        tool_calls = []
        for tc in data.get("message", {}).get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            tool_calls.append({"name": fn.get("name", ""), "arguments": args})
        return {"content": data.get("message", {}).get("content", ""),
                "tool_calls": tool_calls}

    def _complete_openai(self, messages, tools) -> dict:
        body = {"model": self.model, "messages": messages,
                "max_tokens": 4096, "temperature": 0.6}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        candidates = [self.model] + self._sibling_models()
        last_err = None
        for model in candidates:
            body["model"] = model
            r = httpx.post(f"{self.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json=body, timeout=180)
            if r.status_code in (429, 500, 502, 503):
                last_err = f"{model}: {r.status_code}"
                continue
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            tool_calls = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                tool_calls.append({"name": fn.get("name", ""), "arguments": args})
            return {"content": msg.get("content") or "", "tool_calls": tool_calls}
        raise RuntimeError(f"all models unavailable ({last_err}) — provider overloaded, try /model to switch, nya")


class _StreamState:
    """Accumulates streaming deltas: thinking, text, tool-call fragments.

    Handles both provider styles:
    - reasoning deltas: `reasoning_content` / `reasoning` fields (NIM etc.)
    - inline think tags: content wrapped in  (GLM/Qwen style)
    - tool-call fragments keyed by index, arguments arrive in pieces
    """

    def __init__(self):
        self.thinking = ""
        self.text = ""
        self.in_think_tag = False
        self.tools: dict[int, dict] = {}

    _THINK_OPEN = "<" + "think>"    # assembled from parts so transports can't eat the tag
    _THINK_CLOSE = "<" + "/think>"

    def _absorb(self, chunk: str) -> tuple[str, str]:
        """Split think-tagged content → (think_delta, text_delta)."""
        think = text = ""
        buf = chunk
        while buf:
            if not self.in_think_tag and self._THINK_OPEN in buf:
                plain, _, buf = buf.partition(self._THINK_OPEN)
                text += plain
                self.in_think_tag = True
            elif self.in_think_tag and self._THINK_CLOSE in buf:
                thought, _, buf = buf.partition(self._THINK_CLOSE)
                think += thought
                self.in_think_tag = False
                buf = buf.lstrip("\n")
            else:
                if self.in_think_tag:
                    think += buf
                else:
                    text += buf
                buf = ""
        return think, text

    def feed_delta(self, delta: dict) -> dict | None:
        """Consume one delta → an event dict, or None for nothing visible."""
        r = delta.get("reasoning_content") or delta.get("reasoning")
        if r:
            self.thinking += r
            return {"type": "thinking", "delta": r}
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = self.tools.setdefault(idx, {"name": "", "arguments": ""})
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]
        c = delta.get("content")
        if c:
            think, text = self._absorb(c)
            if think:
                self.thinking += think
                return {"type": "thinking", "delta": think}
            if text:
                self.text += text
                return {"type": "text", "delta": text}
        return None

    def feed_sse(self, line: str) -> dict | None:
        """Consume one SSE line from a chat-completions stream."""
        if not line or not line.startswith("data:"):
            return None
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return None
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return None
        choices = chunk.get("choices") or []
        if not choices:
            return None
        return self.feed_delta(choices[0].get("delta") or {})

    def final(self) -> dict:
        """Same shape as Brain.complete(): {content, tool_calls}."""
        calls = []
        for idx in sorted(self.tools):
            slot = self.tools[idx]
            args = slot["arguments"] or "{}"
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
            calls.append({"name": slot["name"], "arguments": args})
        return {"content": self.text, "tool_calls": calls}


# ── streaming extension ──────────────────────────────────────────
def _brain_stream_openai(self, messages, tools):
    """SSE stream → yields thinking/text events, then one final event."""
    body = {"model": self.model, "messages": messages,
            "max_tokens": 4096, "temperature": 0.6, "stream": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    candidates = [self.model] + self._sibling_models()
    last_err = None
    for model in candidates:
        body["model"] = model
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions",
                              headers={"Authorization": f"Bearer {self.api_key}"},
                              json=body, timeout=180) as r:
                if r.status_code in (429, 500, 502, 503):
                    last_err = f"{model}: {r.status_code}"
                    continue
                r.raise_for_status()
                state = _StreamState()
                for line in r.iter_lines():
                    evt = state.feed_sse(line)
                    if evt:
                        yield evt
                final = state.final()
                if not final["tool_calls"] and not final["content"]:
                    # some providers (NIM/nemotron) stream empty on later
                    # tool rounds — heal the turn with a non-streaming retry
                    try:
                        healed = self._complete_openai(messages, tools)
                    except Exception:
                        healed = {"content": "", "tool_calls": []}
                    if healed["content"] or healed["tool_calls"]:
                        if healed["content"]:
                            yield {"type": "text", "delta": healed["content"]}
                        yield {"type": "final", **healed}
                        return
                    last_err = f"{model}: empty stream"
                    continue  # dead model → try a sibling
                yield {"type": "final", **final}
                return
        except httpx.HTTPError as e:
            last_err = f"{model}: {e}"
            continue
    raise RuntimeError(f"all models unavailable ({last_err}) — try /model to switch, nya")


def _brain_stream_ollama(self, messages, tools):
    """Ollama /api/chat NDJSON stream → same event vocabulary."""
    body = {"model": self.model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    tool_calls = []
    text = ""
    with httpx.stream("POST", f"{self.endpoint}/api/chat", json=body, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = chunk.get("message", {})
            th = msg.get("thinking")
            if th:
                yield {"type": "thinking", "delta": th}
            c = msg.get("content", "")
            if c:
                text += c
                yield {"type": "text", "delta": c}
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                tool_calls.append({"name": fn.get("name", ""), "arguments": args})
    yield {"type": "final", "content": text, "tool_calls": tool_calls}


def _brain_complete_stream(self, messages, tools=None):
    """Yield events: thinking | text | final. Same result shape as complete()."""
    if self.type == "ollama":
        yield from _brain_stream_ollama(self, messages, tools)
    else:
        yield from _brain_stream_openai(self, messages, tools)


Brain.complete_stream = _brain_complete_stream  # type: ignore[attr-defined]
