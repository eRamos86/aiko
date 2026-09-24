"""Server brain — reuses the client Brain with a server-local config.

Reads ~/.aikod/config.yaml (server daemon config) instead of ~/.aiko/.
Same provider-agnostic chat completion (NIM/OpenRouter/Ollama/...).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import yaml


def _load_cfg(path: Path | None = None) -> dict:
    p = path or Path.home() / ".aikod" / "config.yaml"
    try:
        return yaml.safe_load(p.read_text()) or {}
    except FileNotFoundError:
        return {}


def _expand(value: str) -> str:
    import os
    import re
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{([A-Z0-9_]+)\}",
                  lambda m: os.environ.get(m.group(1), ""), value)


class ServerBrain:
    """OpenAI-compatible chat completions for the server-side orchestrator."""

    def __init__(self, config_path: Path | None = None):
        cfg = _load_cfg(config_path)
        brains = cfg.get("brains", [])
        if not brains:
            raise RuntimeError(
                "No brains configured — add one to ~/.aikod/config.yaml, nya~")
        default = cfg.get("default_brain")
        brain = next((b for b in brains if b.get("name") == default), brains[0])
        self.name = brain.get("name", "unnamed")
        self.type = brain.get("type", "openai_compatible")
        self.model = brain.get("model", "")
        self.base_url = brain.get("base_url", "").rstrip("/")
        self.endpoint = brain.get("endpoint", "http://localhost:11434").rstrip("/")
        self.api_key = _expand(brain.get("api_key", ""))
        if self.type == "openai_compatible" and not self.api_key:
            raise RuntimeError(f"Brain '{self.name}' has no api_key")

    def describe(self) -> str:
        if self.type == "ollama":
            return f"{self.name} (ollama:{self.model})"
        return f"{self.name} ({self.model})"

    def _sibling_models(self) -> list[str]:
        siblings = []
        for b in _load_cfg().get("brains", []):
            if (b.get("type") == self.type
                    and b.get("base_url", "").rstrip("/") == self.base_url
                    and b.get("model") not in siblings
                    and b.get("model") != self.model):
                siblings.append(b.get("model"))
        return siblings

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One turn. Returns {"content": str, "tool_calls": [...]}."""
        if self.type == "ollama":
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
        raise RuntimeError(
            f"all models unavailable ({last_err}) — provider overloaded, nya")
