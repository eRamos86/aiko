"""Outcome feedback — Aiko's tool selection actually LEARNS from results.

Zero token cost: outcomes come from observable signals (task states,
dispatch → completion latency, failure phrases) — no LLM judging.

Ledger: ~/.aiko/feedback.jsonl (AIKO_FEEDBACK_LEDGER), pruned after 30d.
  {ts, tool, shape, success, duration_s}

modifier_for(tool, shape): once ≥ MIN_ATTEMPTS outcomes exist in the
window, rolling success rate maps to an additive score modifier:
    rate 1.0 → +0.15 (proven tool, promoted)
    rate 0.5 →  0.00 (no opinion yet)
    rate 0.0 → −0.25 (flaky tool, demoted)
  clamped to [−0.30, +0.15]. Selection applies: floor × availability
  + learned modifier. Floors stay user-owned; modifiers drift with reality.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

PRUNE_AFTER = 30 * 86400
MIN_ATTEMPTS = 3
MOD_POS, MOD_NEG = 0.15, -0.30


def _ledger() -> Path:
    return Path(os.environ.get("AIKO_FEEDBACK_LEDGER",
                               Path.home() / ".aiko" / "feedback.jsonl"))


def record_outcome(tool: str, shape: str, success: bool,
                   duration_s: float | None = None) -> None:
    p = _ledger()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "tool": tool, "shape": shape,
                            "success": bool(success),
                            "duration_s": duration_s}) + "\n")


def stats(tool: str, shape: str) -> dict:
    """Rolling (30d) stats for one (tool, task-shape) pair."""
    now = time.time()
    total = wins = 0
    durs: list[float] = []
    p = _ledger()
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if now - e.get("ts", 0) > PRUNE_AFTER:
                continue
            if e.get("tool") == tool and e.get("shape") == shape:
                total += 1
                wins += 1 if e.get("success") else 0
                if e.get("duration_s"):
                    durs.append(float(e["duration_s"]))
    return {"total": total, "wins": wins,
            "rate": (wins / total) if total else None,
            "avg_duration_s": (sum(durs) / len(durs)) if durs else None}


def modifier_for(tool: str, shape: str) -> float:
    """Learned additive modifier; 0 until enough evidence exists."""
    s = stats(tool, shape)
    if s["total"] < MIN_ATTEMPTS or s["rate"] is None:
        return 0.0
    mod = (s["rate"] - 0.5) * 0.5
    return round(max(MOD_NEG, min(MOD_POS, mod)), 3)
