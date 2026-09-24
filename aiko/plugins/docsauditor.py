"""Aiko's thin adapter for the standalone docs-auditor service.

The auditor is NOT part of Aiko — it lives in its own repo/service on the
server (port 4012). This module lets Aiko consume it, like any plugin
consumer: health probe + audit trigger with graceful degradation.
"""
from __future__ import annotations

import httpx

DEFAULT_PORT = 4012


def audit_docs(host: str = "127.0.0.1", port: int = DEFAULT_PORT,
               max_docs: int = 40, auto_fix: bool = True,
               timeout: float = 600.0) -> dict:
    """Trigger a sweep on the standalone docs-auditor service."""
    url = f"http://{host}:{port}/audit"
    try:
        r = httpx.get(url, params={"max_docs": max_docs, "auto_fix": auto_fix},
                      timeout=httpx.Timeout(timeout, connect=5))
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        return {"error": f"docs-auditor not reachable at {url} "
                         "(is `docsauditor serve` running?)"}
    except Exception as e:
        return {"error": f"docs-auditor: {e}"}


def health(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> bool:
    try:
        r = httpx.get(f"http://{host}:{port}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False
