"""Server-side usage economy (twin of aiko/usage.py for the daemon).

Same generic budget model; the daemon's ledger is ~/.aikod/usage.jsonl.
Differences from the client:
  - keys are provider ids from the adapter registry (hermes/codex/agy/…)
  - note_spawn: every CLI worker spawn ≈ 1 request against rolling windows
  - note_transcript_limits: scans finished transcripts for limit-exceeded
    phrases (codex/agy print "usage limit reached", "quota exceeded", …)
    and starts a cooldown so the router hard-skips until the window resets
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

PRUNE_AFTER = 9 * 86400
HARD_SKIP = 10.0

LIMIT_PHRASES = (
    "usage limit reached",
    "usage_limit_reached",
    "quota exceeded",
    "rate limit exceeded",
    "you've hit your limit",
    "you've used all of your",
    "exceeded your weekly limit",
    "limit reached, resets",
    "please try again after",
)

DEFAULT_PROVIDER_BUDGETS = {
    "codex": [{"unit": "requests", "cap": 100, "window": 18000},   # ~5h plan
              {"unit": "requests", "cap": 500, "window": 604800}],  # ~7d plan
    "agy": [{"unit": "requests", "cap": 100, "window": 18000},
            {"unit": "requests", "cap": 500, "window": 604800}],
    "hermes": [],   # API-key based: budgets set per-user in config
}


def _ledger_path() -> Path:
    return Path(os.environ.get("AIKOD_USAGE_LEDGER",
                               Path.home() / ".aikod" / "usage.jsonl"))


def _config_path() -> Path:
    return Path(os.environ.get("AIKOD_USAGE_CONFIG",
                               Path.home() / ".aikod" / "config.yaml"))


def _cooldown_path() -> Path:
    return _ledger_path().with_suffix(".cooldowns.json")


# ── budgets ────────────────────────────────────────────────────

def parse_window(spec: str) -> int:
    s = spec.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    m = re.fullmatch(r"(\d+)\s*([smhdw]?)", s)
    if not m:
        raise ValueError(f"bad window {spec!r}")
    return int(m.group(1)) * mult[m.group(2) or "s"]


def budgets_for(key: str) -> list[dict]:
    import yaml
    p = _config_path()
    entry = None
    try:
        cfg = yaml.safe_load(p.read_text()) or {}
        usage = cfg.get("usage") or {}
        entry = usage.get(key) or (usage.get("providers") or {}).get(key)
    except FileNotFoundError:
        pass
    if isinstance(entry, dict) and entry.get("budgets"):
        return list(entry["budgets"])
    return list(DEFAULT_PROVIDER_BUDGETS.get(key, []))


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
    if pruned:
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in out))
        tmp.replace(p)
    return out


# ── cooldowns ──────────────────────────────────────────────────

def _cooldowns() -> dict:
    p = _cooldown_path()
    try:
        d = json.loads(p.read_text())
        now = time.time()
        return {k: v for k, v in d.items() if v > now}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def in_cooldown(key: str) -> float:
    return max(0.0, _cooldowns().get(key, 0) - time.time())


def _coerce_int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def note_429(key: str, model: str, retry_after=None) -> None:
    record(key, model, "requests", 1, source="429")
    ra = _coerce_int(retry_after) or 60
    cds = _cooldowns()
    cds[key] = time.time() + max(ra, 1)
    _cooldown_path().write_text(json.dumps(cds))


# ── signals ─────────────────────────────────────────────────────

def note_spawn(key: str, model: str) -> None:
    record(key, model, "requests", 1, source="spawn")


def note_transcript_limits(key: str, transcript_text: str,
                          window_seconds: int = 18000) -> float | None:
    """Scan a transcript for limit-exceeded phrases → start cooldown.

    Returns cooldown seconds applied, None when the transcript is clean.
    Rolling-window providers print reset hints ("in 4 hours", "at 5:24 AM")
    but they're unreliable to parse; default cooldown = the 5h window.
    """
    low = transcript_text.lower()
    if not any(p in low for p in LIMIT_PHRASES):
        return None
    cds = _cooldowns()
    until = time.time() + window_seconds
    cds[key] = max(cds.get(key, 0), until)
    _cooldown_path().write_text(json.dumps(cds))
    return float(window_seconds)


# ── math ────────────────────────────────────────────────────────

def window_use(key: str, unit: str, window: int) -> tuple[int, float]:
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
    rows = []
    for b in budgets_for(key):
        used, resets_in = window_use(key, b["unit"], int(b["window"]))
        cap = int(b["cap"])
        rows.append({"unit": b["unit"], "window": int(b["window"]), "cap": cap,
                     "used": used, "remaining": max(0, cap - used),
                     "resets_in": resets_in})
    return rows


def exhaustion(key: str) -> float | None:
    rows = remaining(key)
    if not rows:
        return None
    return min(r["remaining"] / r["cap"] for r in rows if r["cap"] > 0)


def penalty(key: str) -> float:
    """Router penalty: HARD_SKIP in cooldown or exhausted; else 0-0.5 by use."""
    if in_cooldown(key) > 0:
        return HARD_SKIP
    e = exhaustion(key)
    if e is None:
        return 0.0
    if e <= 0:
        return HARD_SKIP
    if e >= 0.5:
        return 0.0
    return round(0.5 * (1 - e / 0.5), 3)


def snapshot_all() -> dict:
    keys = {e["key"] for e in _events()} | set(DEFAULT_PROVIDER_BUDGETS)
    now = time.time()
    out = {}
    for k in sorted(keys):
        raw = {}
        for e in _events():
            if e["key"] == k and now - e["ts"] <= 86400:
                raw[e["unit"]] = raw.get(e["unit"], 0) + e.get("amount", 0)
        out[k] = {"budgets": remaining(k), "raw_24h": raw,
                  "cooldown_s": round(in_cooldown(k))}
    return out


def set_budget(key: str, unit: str, cap: int, window: str) -> dict:
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
