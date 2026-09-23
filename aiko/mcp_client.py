"""Aiko's own MCP client — configurable MCP servers, called during chat.

Config (mcp_servers:) → {name: {url, headers}}. Streamable HTTP transport.
Tools surface as mcp_<server>_<tool> for the orchestrator's brain; each call
is a JSON-RPC POST with an id, and results come back as text content.

This is a minimal, dependency-free client (httpx only) — enough for tool
calling with streamable HTTP MCP servers (the vault MCP speaks this).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

_RPC_ID = 0


def _load_cfg() -> dict:
    p = Path.home() / ".aiko" / "config.yaml"
    try:
        return yaml.safe_load(p.read_text()) or {}
    except FileNotFoundError:
        return {}


def _expand(value: str) -> str:
    import os
    import re
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), value)


class McpClient:
    """Streamable-HTTP MCP client for one server."""

    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url
        self.headers = {k: _expand(v) for k, v in (headers or {}).items()}
        self._tools: list[dict] | None = None
        self._session_header: str | None = None

    def _post(self, payload: dict, timeout: float = 30) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        headers.update(self.headers)
        # session id from a previous initialize, if the server issued one
        if self._session_header:
            headers["Mcp-Session-Id"] = self._session_header
        r = httpx.post(self.url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        if sid := r.headers.get("mcp-session-id"):
            self._session_header = sid
        # streamable HTTP may answer with SSE; take the data line
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise RuntimeError("SSE response with no data line")
        return r.json()

    def list_tools(self) -> list[dict]:
        """tools/list → [{name, description}]"""
        global _RPC_ID
        if self._tools is not None:
            return self._tools
        _RPC_ID += 1
        resp = self._post({
            "jsonrpc": "2.0", "id": _RPC_ID, "method": "tools/list",
        })
        raw_tools = (resp.get("result") or {}).get("tools") or []
        self._tools = [t for t in raw_tools if isinstance(t, dict)]
        return self._tools

    def call_tool(self, tool: str, args: dict) -> str:
        """tools/call → concatenated text content"""
        global _RPC_ID
        _RPC_ID += 1
        resp = self._post({
            "jsonrpc": "2.0", "id": _RPC_ID, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
        result = resp.get("result") or {}
        if result.get("isError"):
            parts = [c.get("text", "") for c in result.get("content", [])]
            raise RuntimeError("; ".join(parts) or "MCP tool error")
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(p for p in parts if p)

    def tools_spec(self) -> list[dict]:
        """OpenAI-style tool specs for the brain."""
        spec = []
        for t in self.list_tools():
            params = (t.get("inputSchema") or {"type": "object", "properties": {}})
            spec.append({
                "type": "function",
                "function": {
                    "name": f"mcp_{self.name}_{t['name']}",
                    "description": t.get("description", "")[:400] or f"MCP tool {t['name']}",
                    "parameters": params,
                },
            })
        return spec

    def dispatch(self, full_name: str, args: dict) -> str:
        """Route mcp_<server>_<tool> calls to the right server+tool."""
        prefix = f"mcp_{self.name}_"
        if not full_name.startswith(prefix):
            raise KeyError(full_name)
        tool = full_name[len(prefix):]
        return self.call_tool(tool, args)


def load_mcp_clients() -> dict[str, McpClient]:
    """Build clients from ~/.aiko/config.yaml mcp_servers: section."""
    cfg = _load_cfg()
    clients: dict[str, McpClient] = {}
    for name, entry in (cfg.get("mcp_servers") or {}).items():
        if not isinstance(entry, dict) or not entry.get("url"):
            continue
        clients[name] = McpClient(name, entry["url"], entry.get("headers"))
    return clients


def mcp_tools_spec(clients: dict[str, McpClient]) -> list[dict]:
    specs: list[dict] = []
    for c in clients.values():
        try:
            specs.extend(c.tools_spec())
        except Exception:
            continue  # a dead MCP server shouldn't kill the chat
    return specs


def mcp_dispatch(clients: dict[str, McpClient], name: str, args: dict) -> str:
    for c in clients.values():
        if name.startswith(f"mcp_{c.name}_"):
            return c.dispatch(name, args)
    raise KeyError(f"no MCP server for tool {name}")
