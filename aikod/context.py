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
    """Assemble the context bundle for a task: spec + retrieved vault slices.

    v0.1 retrieval: keyword search off the spec + any explicit hint paths
    (project hub, protocol docs). No embeddings (Q10).
    """
    parts = [f"# Task\n{task_spec}\n"]

    # Always include the universal hub + shared context (small, high-value).
    for must in ("Agents/Agents.md", "Agents/Shared-Context.md"):
        content = fetch_doc(must)
        if content:
            parts.append(f"---\n# Vault: {must}\n{content}\n")

    # Hinted docs (e.g. the project's hub for the repo being worked on).
    for hint in hints or []:
        content = fetch_doc(hint)
        if content:
            parts.append(f"---\n# Vault: {hint}\n{content}\n")

    return "\n".join(parts)
