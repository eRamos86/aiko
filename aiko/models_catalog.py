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

# (regex on model id, {persona: additive boost}) — seeds only; the feedback
# ledger is the real signal. Many buckets by user request: any model can be
# "the right one" for somebody's specific ask.
PERSONAS = ("code", "debug", "research", "plan", "write", "data",
            "support", "chat", "teach", "devops")

PRIOR_RULES = [
    (r"coder|code|starcoder|devstral|codestral|codellama|codegemma|codeqwen",
     {"code": 0.30, "debug": 0.20, "devops": 0.10}),
    (r"reason(ing)?|think|-r1|qwq|deepseek-r01|deepseek-r1",
     {"research": 0.25, "plan": 0.28, "debug": 0.15, "data": 0.10}),
    (r"ultra|large|405b|120b|550b|70b|671b|\bpro\b|opus",
     {"research": 0.12, "plan": 0.12, "write": 0.08, "teach": 0.05}),
    (r"nano|mini|flash|lite|small|8b|7b|4b|3b|2b|1\.5b|0\.6b",
     {"chat": 0.22, "support": 0.08}),
    (r"instruct|chat|it\b", {"chat": 0.12, "support": 0.10, "teach": 0.08}),
    (r"writer|creative|story|nova", {"write": 0.20, "support": 0.06}),
    (r"mistral|mixtral|llama-3\.3|command-a|gemma3",
     {"write": 0.10, "chat": 0.12, "support": 0.08, "research": 0.06}),
    (r"math|finance|deepseek-math", {"data": 0.25, "research": 0.10}),
    (r"nomadic|wizard|dolphin", {"support": 0.15, "write": 0.10, "chat": 0.08}),
]

BASE_PRIORS = {p: 0.5 for p in PERSONAS}


_CACHE_MEMORY: tuple[float, dict] | None = None


def _load_cache() -> dict | None:
    """24h-cached catalog payload (models + any extras like pull variants)."""
    global _CACHE_MEMORY
    if _CACHE_MEMORY and time.time() - _CACHE_MEMORY[0] < CACHE_TTL:
        return _CACHE_MEMORY[1]
    try:
        cached = json.loads(CACHE_PATH.read_text())
        if time.time() - cached.get("ts", 0) < CACHE_TTL:
            _CACHE_MEMORY = (float(cached["ts"]), cached)
            return cached
    except Exception:
        pass
    return None


def _save_cache(models: dict[str, list[str]], extra: dict | None = None) -> None:
    global _CACHE_MEMORY
    payload: dict = {"ts": time.time(), "models": models}
    if extra:
        payload.update(extra)
    _CACHE_MEMORY = (payload["ts"], payload)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload))


def priors_for(model_id: str) -> dict:
    p = dict(BASE_PRIORS)
    for rx, boosts in PRIOR_RULES:
        if re.search(rx, model_id, re.I):
            for shape, b in boosts.items():
                p[shape] = min(1.0, p.get(shape, 0.5) + b)
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


_PARAM_RE = re.compile(r"^([a-z0-9._-]+):(\d+(?:\.\d+)?)([bm])(?:[-_.a-z0-9]*)?$", re.I)
# rough VRAM/RAM estimate per param count at q4 (GiB): params(B) × 0.55 + 15% overhead
def _gb_needed(params_b: float) -> float:
    return round(params_b * 0.55 * 1.15, 1)


def _free_ram_gb() -> float:
    """Usable system RAM in GiB — sysctl hw.memsize × a conservative 0.5
    (we don't want to starve the OS/apps), no external deps."""
    import subprocess
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return round(int(out.stdout.strip()) / 1e9 * 0.5, 1)
    except Exception:
        pass
    return 8.0  # conservative fallback


def registry_pull_models(limit: int = 24, force: bool = False) -> list[dict]:
    """Concrete pullable VARIANTS with size estimates, filtered to fit.

    Returns [{model, tag, params_b, gb, fits}] sorted by gb asc then name.
    Cached 24h together with catalog()."""
    cached = None if force else _load_cache()
    key = "ollama_pull_variants"
    if cached is not None and key in cached:
        return cached[key]
    try:
        import httpx
    except ImportError:
        return []
    local = set(catalog0_names())
    base_names = _fetch_ollama_installable(local)[:limit]
    free = _free_ram_gb()
    rows: list[dict] = []
    for name in base_names:
        try:
            r = httpx.get(f"https://ollama.com/library/{name}/tags",
                          timeout=12, headers={"User-Agent": "aiko-catalog/1.0"})
            r.raise_for_status()
            tags = set(re.findall(rf"/library/{re.escape(name)}:([a-z0-9][a-z0-9._-]*)",
                                  r.text))
            for tag in tags or {"latest"}:
                full = f"{name}:{tag}"
                if full in local:
                    continue
                m = _PARAM_RE.match(full)
                if m:
                    params = float(m.group(2)) * (1e9 if m.group(3).lower() == "b"
                                                  else 1e6)
                    gb = _gb_needed(params / 1e9)
                else:
                    params = 0.0
                    gb = 0.0  # unknown → list last, never block
                rows.append({"model": full, "tag": tag, "params_b": params,
                             "gb": gb, "fits": gb <= free})
        except Exception:
            continue
    # dedupe: per family keep the smallest FITTING concrete tag AND the
    # biggest (flagged ⚠). `latest` is only a fallback when no concrete
    # param-tagged variant exists — it's opaque (unknown size).
    keep: dict[str, dict] = {}
    loose: dict[str, dict] = {}
    for r in rows:
        fam = r["model"].split(":", 1)[0]
        target = keep if r["fits"] else loose
        cur = target.get(fam)
        if r["tag"] == "latest":
            if cur is None:                # only fill if no sized variant
                target[fam] = r
        elif r["params_b"] > 0:            # concrete sized variant
            if cur is None or cur["tag"] == "latest":
                target[fam] = r
            elif cur["gb"] and r["gb"] < cur["gb"]:
                target[fam] = r
        elif cur is None:
            target[fam] = r
    out = sorted(list(keep.values()) + list(loose.values()),
                 key=lambda r: (not r["fits"],
                                r["gb"] if r["gb"] > 0 else 9999.0,
                                r["model"]))
    if cached is not None:
        cached[key] = out
        _save_cache(cached.get("models", {}), extra={key: out})
    return out


def catalog(force: bool = False) -> dict[str, list[str]]:
    """{provider_key: [model ids]} — cached 24h; providers from config creds.

    Extra key `ollama_installable` = popular registry pull family names."""
    cached = None if force else _load_cache()
    if cached is not None and "models" in cached:
        models = {k: v for k, v in cached["models"].items()}
        if models.get("ollama"):
            models.setdefault("ollama_installable",
                              _fetch_ollama_installable(set(models["ollama"])))
        return models
    models: dict[str, list[str]] = {}
    for key, brain in _provider_brains().items():
        ids = _fetch_provider(key, brain)
        if ids:
            models[key] = sorted(set(ids))
    if models.get("ollama"):
        models["ollama_installable"] = _fetch_ollama_installable(
            set(models["ollama"]))
    _save_cache(models)
    return models


def catalog0_names() -> list[str]:
    """Just the installed ollama names (helper for pull-variant filter)."""
    return catalog().get("ollama", [])


def rank_models(task_shape: str, limit: int = 12) -> list[dict]:
    """Rank EVERY known model for a persona: (seed|prior) × 0.4 + learned.

    score = 0.4 × max(seed_bench, name-prior) + 0.6 × learned modifier
    Seeds (aiko/seed_scores.py) are cold-start quality only; the feedback
    ledger is the user's real signal and dominates once it has data.
    """
    from .feedback import modifier_for
    from .seed_scores import seed_scores
    rows = []
    for prov, ids in catalog().items():
        if prov == "ollama_installable":
            continue
        for m in ids:
            seeded = seed_scores(m).get(task_shape, 0.0)
            prior = max(seeded, priors_for(m).get(task_shape, 0.5))
            fb = modifier_for("self", task_shape, model=m)
            score = round(max(0.0, min(1.0, prior * 0.4 + fb * 0.6 + 0.3)), 3)
            rows.append({"provider": prov, "model": m, "score": score,
                         "prior": round(prior, 3), "feedback": fb,
                         "why": f"seed|prior={prior:.2f}, learned={fb:+.2f}"})
    rows.sort(key=lambda r: (-r["score"], r["model"]))
    return rows[:limit]
