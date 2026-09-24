"""Aiko usage economy — every provider limits differently, one model fits all.

Budgets are generic: {unit, cap, window_seconds}.
  - codex/agy rolling plans → [{requests, N, 5h}, {requests, M, 7d}]
  - NIM                      → [{requests, 40, 60s}]
  - OpenRouter               → [{requests, RPM, 60s}, {credits, C, 7d}]
Caps live in config (`usage:`) — every user's plan is different; known
providers get default budgets automatically.

Signals that feed the ledger (~/.aiko/usage.jsonl, or AIKO_USAGE_LEDGER):
  - every completion: 1 request + token usage from the API response
  - 429s: request + cooldown (Retry-After honored) → router hard-skips
  - CLI agent spawns: 1 request event (daemon side)
  - limit-exceeded phrases in worker transcripts → cooldown (daemon side)

Readers:
  - aiko Brain + aikod ServerBrain: harvest per completion
  - aikod router: utilization penalty (0 at ≤50% used → 0.5 at 100%),
    hard-skip exhausted/cooldown providers
  - `/usage` in the TUI and `aiko usage` CLI: the human view
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

PRUNE_AFTER = 9 * 86400  # keep 9 days (covers 7d windows + margin)

KNOWN_PROVIDERS = {
    "nim": {
        "match": "integrate.api.nvidia.com",
        "budgets": [{"unit": "requests", "cap": 40, "window": 60}],
    },
    "openrouter": {
        "match": "openrouter.ai",
        "budgets": [{"unit": "requests", "cap": 200, "window": 60}],
    },
}


def _ledger_path() -> Path:
    return Path(os.environ.get("AIKO_USAGE_LEDGER",
                               Path.home() / ".aiko" / "usage.jsonl"))


def _config_path() -> Path:
    return Path(os.environ.get("AIKO_USAGE_CONFIG",
                               Path.home() / ".aiko" / "config.yaml"))


def _cooldown_path() -> Path:
    return _ledger_path().with_suffix(".cooldowns.json")


def provider_key(base_url: str, model: str = "") -> str:
    """Map a base_url to a stable usage key (nim, openrouter, host...)."""
    for name, meta in KNOWN_PROVIDERS.items():
        if meta["match"] in (base_url or ""):
            return name
    m = re.match(r"https?://([^/]+)", base_url or "")
    return m.group(1) if m else (base_url or "unknown")


# ── config ──────────────────────────────────────────────────────

def _load_cfg() -> dict:
    import yaml
    p = _config_path()
    try:
        return yaml.safe_load(p.read_text()) or {}
    except FileNotFoundError:
        return {}


def parse_window(spec: str) -> int:
    """'40m' → 2400, '5h' → 18000, '7d' → 604800, '60' → 60."""
    s = spec.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    m = re.fullmatch(r"(\d+)\s*([smhdw]?)", s)
    if not m:
        raise ValueError(f"bad window {spec!r} — use 60, 40m, 5h, 7d…")
    return int(m.group(1)) * mult[m.group(2) or "s"]


def fmt_window(seconds: int) -> str:
    for size, label in ((604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds % size == 0 and seconds >= size:
            return f"{seconds // size}{label}"
    return f"{seconds}s"


def budgets_for(key: str) -> list[dict]:
    """Config-declared budgets win; known providers fall back to defaults."""
    cfg = _load_cfg().get("usage") or {}
    entry = cfg.get(key) or (cfg.get("providers") or {}).get(key)
    if isinstance(entry, dict) and entry.get("budgets"):
        return list(entry["budgets"])
    meta = KNOWN_PROVIDERS.get(key)
    return list(meta["budgets"]) if meta else []


# ── ledger ──────────────────────────────────────────────────────

def record(key: str, model: str, unit: str, amount, source: str = "api") -> None:
    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "key": key, "model": model,
                            "unit": unit, "amount": amount,
                            "source": source}) + "\n")


def _events() -> list[dict]:
    p = _ledger_path()
    if not p.exists():
        return []
    now = time.time()
    out, pruned = [], False
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            pruned = True
            continue
        if now - e.get("ts", 0) > PRUNE_AFTER:
            pruned = True
            continue
        out.append(e)
    if pruned:  # rewrite compacted
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in out))
        tmp.replace(p)
    return out


# ── cooldowns (429 / limit-exceeded) ────────────────────────────

def _cooldowns() -> dict:
    p = _cooldown_path()
    try:
        d = json.loads(p.read_text())
        now = time.time()
        return {k: v for k, v in d.items() if v > now}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def in_cooldown(key: str) -> float:
    """Seconds remaining in cooldown, 0 if none."""
    return max(0.0, _cooldowns().get(key, 0) - time.time())


def _coerce_int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def note_429(key: str, model: str, retry_after=None) -> None:
    record(key, model, "requests", 1, source="429")
    ra = _coerce_int(retry_after) or 60
    p = _cooldown_path()
    cds = _cooldowns()
    cds[key] = time.time() + max(ra, 1)
    p.write_text(json.dumps(cds))


# ── harvesting (called by Brain / ServerBrain / daemon) ─────────

def note_response(key: str, model: str, headers=None, usage=None) -> None:
    """One completed API call: 1 request + token usage when reported."""
    record(key, model, "requests", 1, source="api")
    if usage:
        total = (usage.get("prompt_tokens") or 0) + \
                (usage.get("completion_tokens") or 0)
        if total:
            record(key, model, "tokens", total, source="api")


def note_spawn(key: str, model: str) -> None:
    """A CLI agent spawn (codex/agy/hermes one-shot) ≈ 1 against its window."""
    record(key, model, "requests", 1, source="spawn")


# ── math ────────────────────────────────────────────────────────

def window_use(key: str, unit: str, window: int) -> tuple[int, float]:
    """(amount used in the rolling window, seconds until oldest event ages out)."""
    now = time.time()
    oldest = None
    total = 0
    for e in _events():
        if e["key"] == key and e["unit"] == unit and now - e["ts"] <= window:
            total += e.get("amount", 0)
            oldest = e["ts"] if oldest is None else min(oldest, e["ts"])
    resets_in = window if oldest is None else max(0.0, window - (now - oldest))
    return total, resets_in


def remaining(key: str) -> list[dict]:
    """Per-budget view: used / cap / remaining / resets_in."""
    rows = []
    for b in budgets_for(key):
        used, resets_in = window_use(key, b["unit"], int(b["window"]))
        cap = int(b["cap"])
        rows.append({"unit": b["unit"], "window": int(b["window"]), "cap": cap,
                     "used": used, "remaining": max(0, cap - used),
                     "resets_in": resets_in})
    return rows


def exhaustion(key: str) -> float | None:
    """Min remaining fraction across budgets (1 = fresh, 0 = spent).
    None when no budgets are declared → no penalty, ledger still counts."""
    rows = remaining(key)
    if not rows:
        return None
    return min(r["remaining"] / r["cap"] for r in rows if r["cap"] > 0)


HARD_SKIP = 10.0


def penalty(key: str) -> float:
    """Router penalty. HARD_SKIP when cooling down or fully spent."""
    if in_cooldown(key) > 0:
        return HARD_SKIP
    e = exhaustion(key)
    if e is None:
        return 0.0
    if e <= 0:
        return HARD_SKIP
    if e >= 0.5:
        return 0.0
    return round(0.5 * (1 - e / 0.5), 3)   # 0 → 0.5 as use goes 50% → 100%


def snapshot_all() -> dict:
    """The human view: every known key, budgets + raw counters + cooldowns."""
    cfg = _load_cfg().get("usage") or {}
    keys = set(cfg.keys()) | set((cfg.get("providers") or {}).keys())
    keys |= {e["key"] for e in _events()}
    keys.discard("providers")
    now = time.time()
    out = {}
    for k in sorted(keys):
        raw_units = {}
        for e in _events():
            if e["key"] == k and now - e["ts"] <= 86400:
                raw_units[e["unit"]] = raw_units.get(e["unit"], 0) + e.get("amount", 0)
        out[k] = {"budgets": remaining(k), "raw_24h": raw_units,
                  "cooldown_s": round(in_cooldown(k))}
    return out


def set_budget(key: str, unit: str, cap: int, window: str) -> dict:
    """`aiko usage-set key requests 40 5h` → writes config, replaces same
    unit+window if present. Every user's plan is different; this is the knob."""
    import yaml
    p = _config_path()
    cfg = {}
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    usage = cfg.setdefault("usage", {})
    if "providers" in usage and key in usage["providers"]:
        target = usage["providers"][key]
    else:
        target = usage.setdefault(key, {})
    w = parse_window(window)
    budgets = [b for b in target.get("budgets", [])
               if not (b.get("unit") == unit and int(b.get("window", -1)) == w)]
    budgets.append({"unit": unit, "cap": int(cap), "window": w})
    target["budgets"] = budgets
    p.parent.mkdir(parents=True, exist_ok=True)
    yaml.dump(cfg, p.open("w"), sort_keys=False)
    return {"key": key, "unit": unit, "cap": int(cap), "window": w}
