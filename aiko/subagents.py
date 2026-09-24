"""Universal subagents (ADR-018): vault-defined specialists spawnable by
ANY agent in the pool — Aiko included, opencode included.

Source of truth: Agents/Agent-Registry.yaml in the vault (server copy;
local clone is read-mostly). Each subagent has a prompt body + traits.

Spawn policy (no hard depth caps — usage economy governs, per user):
  - "light" subagents (planner, critic, researcher, summarizer...) run
    in-process on the caller's own brain unless prefer says otherwise —
    cheapest path, keeps context local.
  - "heavy" subagents (codewriter, debugger, refactorer...) dispatch to a
    worker agent via the caller's backend (local dispatch or aikod).
  - Every spawn records an outcome in the feedback ledger keyed by
    (subagent name, shape) so host selection learns.
  - Depth is tracked and reported in the spawn metadata; nothing blocks
    deep chains, but each hop spends real budget (usage tracker) and the
    selection engine will naturally prefer cheaper hosts for sub-work.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .feedback import record_outcome

REGISTRY = (Path.home() / "Documents" / "Obsidian" / "obsidian-main"
            / "Agents" / "Agent-Registry.yaml")

# Trait buckets → default host style.
_LIGHT = {"planner", "critic", "researcher", "summarizer", "reviewer",
          "writer", "analyst", "qa"}
_HEAVY = {"codewriter", "debugger", "refactorer", "tester", "devops"}


def load_subagent(name: str) -> dict:
    """Load a subagent definition from the vault registry."""
    import yaml
    if not REGISTRY.exists():
        raise ValueError(f"registry not found: {REGISTRY}")
    reg = yaml.safe_load(REGISTRY.read_text()) or {}
    agents = reg.get("subagents") or reg.get("agents") or []
    if isinstance(agents, dict):
        entry = agents.get(name)
        if entry and isinstance(entry, dict):
            return {"name": name, **entry}
    for a in agents:
        if isinstance(a, dict) and a.get("name") == name:
            return a
    raise ValueError(f"unknown subagent {name!r} (check Agents/Agent-Registry.yaml)")


def list_subagents() -> list[dict]:
    import yaml
    if not REGISTRY.exists():
        return []
    reg = yaml.safe_load(REGISTRY.read_text()) or {}
    agents = reg.get("subagents") or reg.get("agents") or []
    if isinstance(agents, dict):
        return [{"name": k, **(v or {})} for k, v in agents.items()]
    return [a for a in agents if isinstance(a, dict)]


def _depth(prompt: str) -> int:
    """Track chain depth via a marker the caller embeds."""
    import re
    m = re.search(r"\[spawn-depth:(\d+)\]", prompt)
    return int(m.group(1)) if m else 0


def spawn(caller, spec: dict, prompt: str, prefer: str | None = None) -> dict:
    """Spawn one subagent. `caller` is the Orchestrator (has .brain/.backend.)

    Returns {host, subagent, result|task_id, depth, duration_s}.
    """
    name = spec.get("name", "subagent")
    body = spec.get("prompt") or spec.get("body") or ""
    tools = spec.get("tools") or []
    depth = _depth(prompt) + 1
    sys_prompt = (f"You are {name}, a specialist subagent in Ace's agent "
                  f"pool. {body}\n\nStay strictly in-role; return concise, "
                  f"actionable output. Depth marker: [spawn-depth:{depth}]")
    started = time.time()

    is_light = name.lower() in _LIGHT or any(t in _LIGHT for t in tools)
    host_pref = prefer or ("self" if is_light else None)

    result: dict = {"host": "self", "subagent": name, "depth": depth}
    success = False
    try:
        if host_pref == "self" or host_pref is None and is_light:
            r = caller.brain.complete(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": prompt}])
            result["result"] = (r.get("content") or "").strip()
            result["model"] = getattr(caller.brain, "name", "?")
            success = bool(result["result"])
        else:
            target = None if host_pref in (None, "self") else host_pref
            full = sys_prompt + "\n\n# Task\n" + prompt
            dispatched = caller.backend.dispatch(full, target)
            result.update({"host": dispatched.get("target", target or "auto"),
                           "task_id": dispatched.get("task_id")})
            success = not dispatched.get("error")
            if dispatched.get("error"):
                result["error"] = dispatched["error"]
    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        result["duration_s"] = round(time.time() - started, 2)
        try:
            record_outcome(f"subagent:{name}", "subagent", success,
                           duration_s=result["duration_s"],
                           model=result.get("model"))
        except Exception:
            pass
    return result
