"""Context providers — optional docs attachment (plugin, not core).

Config (context_providers:) lists any number of providers. Supported type:
  http_docs: GET {base_url}/api/vault/doc?path=... with headers, expects
             {"content": "..."}. (The Obsidian vault API speaks this, but any
             read API that matches works — it's generic.)
Aiko attaches each provider's `attach:` docs to dispatched task specs.
No providers configured? Everything works fine — dispatch just goes bare.
"""
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
    import os
    import re
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{([A-Z0-9_]+)\}",
                  lambda m: os.environ.get(m.group(1), ""), value)


def fetch_doc(provider: dict, path: str) -> str | None:
    """Fetch one doc from one provider. Returns content or None."""
    ptype = provider.get("type")
    if ptype != "http_docs":
        return None
    base = provider.get("base_url", "").rstrip("/")
    headers = {k: _expand(v) for k, v in provider.get("headers", {}).items()}
    try:
        r = httpx.get(f"{base}/api/vault/doc", params={"path": path},
                      headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json().get("content")
    except httpx.HTTPError:
        pass
    return None


def assemble_context(task_spec: str) -> str:
    """Attach every provider's `attach:` docs to a task spec (for dispatch)."""
    cfg = _load_cfg()
    parts = [task_spec]
    for provider in cfg.get("context_providers", []):
        for doc_path in provider.get("attach", []):
            content = fetch_doc(provider, doc_path)
            if content:
                parts.append(f"---\n# Context: {doc_path}\n{content}\n")
    return "\n".join(parts) if len(parts) > 1 else task_spec
