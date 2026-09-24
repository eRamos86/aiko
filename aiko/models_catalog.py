"""Model catalog discovery + cheap capability priors (ADR-017).

Aiko should know every model her providers offer (NIM has 82+ right now),
rank them per task shape, and LEARN which ones actually work — without
hardcoded per-model logic.

Three layers:
  1. catalog() — live discovery from configured providers (NIM /v1/models,
     Ollama /api/tags; cached 24h in ~/.aiko/models_catalog.json)
  2. priors_for(model_id) — cheap name/metadata heuristics: reasoning models
     get research/plan boosts, coders get implement, nano/flash get trivial.
     Only a SEED — not the truth.
  3. the feedback ledger (tool='self', model, shape) — real outcomes drift
     the ranking; exploration (selection.py) samples under-tried models.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

CACHE_PATH = Path.home() / ".aiko" / "models_catalog.json"
CACHE_TTL = 86400  # 24h

# (regex on model id, {shape: additive boost}) — seeds only, learning overrides
PRIOR_RULES = [
    (r"reason(ing)?|think|-r1\b|qwq|deepseek-r", {"research": 0.2, "plan": 0.25, "debug": 0.2}),
    (r"coder|code|starcoder|devstral|codestral", {"implement": 0.25, "debug": 0.15, "review": 0.1}),
    (r"nano|mini|flash|lite|8b|7b|4b|3b|small", {"trivial": 0.25, "chat": 0.2}),
    (r"ultra|large|pro\b|120b|550b|70b|405b", {"research": 0.1, "plan": 0.1, "write_docs": 0.05}),
    (r"vl|-omni|vision|llava", {"research": 0.05}),
]

BASE_PRIORS = {"trivial": 0.5, "chat": 0.5, "research": 0.5, "plan": 0.5,
               "implement": 0.5, "debug": 0.5, "review": 0.5, "write_docs": 0.5}


def priors_for(model_id: str) -> dict:
    p = dict(BASE_PRIORS)
    for rx, boosts in PRIOR_RULES:
        if re.search(rx, model_id, re.I):
            for shape, b in boosts.items():
                p[shape] = min(1.0, p[shape] + b)
    return p


def _provider_brains() -> dict[str, dict]:
    """Configured brains grouped by provider key (creds source)."""
    from .brain import list_brains
    from .usage import provider_key
    out: dict[str, dict] = {}
    for b in list_brains():
        key = provider_key(b.get("base_url") or "", ) or "unknown"
        if b.get("type") == "ollama":
            key = "ollama"
        out.setdefault(key, b)
    return out


def _fetch_provider(key: str, brain: dict) -> list[str] | None:
    import httpx
    try:
        if key == "ollama":
            host = (brain.get("endpoint") or "http://localhost:11434").rstrip("/")
            r = httpx.get(f"{host}/api/tags", timeout=15)
            return [m["name"] for m in r.json().get("models", [])]
        base = (brain.get("base_url") or "").rstrip("/")
        r = httpx.get(f"{base}/models",
                      headers={"Authorization": f"Bearer {brain.get('api_key','')}"},
                      timeout=20)
        if r.status_code != 200:
            return None
        return [m.get("id", "?") for m in r.json().get("data", [])]
    except Exception:
        return None


def _fetch_ollama_installable(local: set[str]) -> list[str]:
    """Popular pullable models from ollama.com/library, minus installed."""
    import httpx
    try:
        r = httpx.get("https://ollama.com/library", timeout=15,
                      headers={"User-Agent": "aiko-catalog/1.0"})
        r.raise_for_status()
        names = set(re.findall(r"/library/([a-z0-9][a-z0-9._-]*)", r.text))
        junk = {"search", "blog", "docs", "download", "signin", "signup",
                "latest", "tags", "models", "api", "library"}
        found = sorted(n for n in names if n not in junk)
        return [n for n in found if n not in local][:40]
    except Exception:
        return [m for m in ["llama3.3:70b", "qwen3:32b", "qwen3-coder:30b",
                            "deepseek-r1:14b", "mistral:7b", "gemma3:12b"]
                if m not in local]


def catalog(force: bool = False) -> dict[str, list[str]]:
    """{provider_key: [model ids]} — cached 24h; providers from config creds.

    Extra key `ollama_installable` = popular registry pulls not yet local."""
    now = time.time()
    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
            if now - cached.get("ts", 0) < CACHE_TTL:
                return cached["models"]
        except json.JSONDecodeError:
            pass
    models: dict[str, list[str]] = {}
    for key, brain in _provider_brains().items():
        ids = _fetch_provider(key, brain)
        if ids:
            models[key] = sorted(set(ids))
    if models.get("ollama"):
        models["ollama_installable"] = _fetch_ollama_installable(
            set(models["ollama"]))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({"ts": now, "models": models}))
    return models


def rank_models(task_shape: str, limit: int = 12) -> list[dict]:
    """Rank EVERY known model for a shape: prior + learned modifier.

    score = prior(shape) + learned (tool='self', model, shape)
    — exploration happens in selection.recommend, not here.
    """
    from .feedback import modifier_for
    rows = []
    for prov, ids in catalog().items():
        if prov == "ollama_installable":
            continue  # ranking is for usable-now models
        for m in ids:
            prior = priors_for(m).get(task_shape, 0.5)
            fb = modifier_for("self", task_shape, model=m)
            score = round(max(0.0, min(1.0, prior + fb)), 3)
            rows.append({"provider": prov, "model": m, "score": score,
                         "prior": round(prior, 3), "feedback": fb,
                         "why": f"prior={prior:.2f} + learned={fb:+.2f}"})
    rows.sort(key=lambda r: (-r["score"], r["model"]))
    return rows[:limit]
