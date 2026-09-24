"""Context bundle assembly via the Vault API (H7)."""
import os
from pathlib import Path

import requests

VAULT_API = os.environ.get("VAULT_API_URL", "http://127.0.0.1:4010")
VAULT_SECRET = os.environ.get("VAULT_API_SECRET", "")


def _headers():
    return {"X-Vault-Secret": VAULT_SECRET}


def fetch_doc(path: str) -> str | None:
    """Fetch a single vault doc's content. Returns None on miss/deny."""
    try:
        r = requests.get(f"{VAULT_API}/api/vault/doc",
                         params={"path": path}, headers=_headers(), timeout=10)
        if r.ok:
            return r.json().get("content")
    except requests.RequestException:
        pass
    return None


def search(query: str, limit: int = 5) -> list[dict]:
    try:
        r = requests.get(f"{VAULT_API}/api/vault/search",
                         params={"q": query, "limit": limit},
                         headers=_headers(), timeout=10)
        if r.ok:
            return r.json().get("results", [])
    except requests.RequestException:
        pass
    return []


def assemble_bundle(task_spec: str, hints: list[str] | None = None) -> str:
    """Assemble the context bundle for a task — PROGRESSIVE (ADR-014).

    Tier 0 (always): task spec + Context Router (~2KB) — the map that says
      what to load and how to fetch it (MCP read_note/search_notes, or the
      REST API). NOTHING else rides along uninvited.
    Tier 1 (agent-driven): the agent reads the router and lazily fetches
      what the task actually needs — a named project's Platform/<P>/ hub,
      operating rules, protocols — through its vault MCP.
    Explicit `hints` (caller-supplied paths) are still honored for cases
      where the dispatcher already knows what's relevant.
    """
    parts = [f"# Task\n{task_spec}\n"]

    # Tier 0: the router is the universal layer's *index* — small on purpose.
    router = fetch_doc("Agents/Context Router.md")
    if router:
        parts.append(f"---\n# Vault: Agents/Context Router.md\n{router}\n")

    # Caller-known relevant docs (explicit hints) — never guessed.
    for hint in hints or []:
        content = fetch_doc(hint)
        if content:
            parts.append(f"---\n# Vault: {hint}\n{content}\n")

    return "\n".join(parts)
