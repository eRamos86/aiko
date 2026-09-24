"""Aiko tool selection — she knows what tool is best for a task.

One ranked list across ALL options:
  - answering herself (each configured brain, at each reasoning level)
  - dispatching to local agents (codex/agy/hermes/opencode…)
  - dispatching to servers (aikod, which routes to ITS agents)

Score = capability_floor × availability, where:
  capability_floor  — per task_type, from tool_capabilities.yaml (user-tunable)
  availability      — usage economy: budget remaining, cooldowns, health

The triviality gate: small tasks (haiku, quick Qs, tiny edits) she just
DOES — her own brain at the right reasoning level, zero dispatch. An
orchestrator that can't write her own haiku is just a router, nya.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CAPS_PATH = Path.home() / ".aiko" / "tool_capabilities.yaml"

# Reasonable defaults; users tune via the yaml. Scores are 0-1 capability
# floors per task shape. `self:<brain>` entries = Aiko answering directly.
DEFAULT_CAPS = {
    "self": {          # Aiko's own brain (any provider) — great at trivia,
        # light writing, quick reasoning. Heavy/long work → dispatch.
        "research": 0.55, "plan": 0.65, "implement": 0.45,
        "debug": 0.5, "review": 0.6, "write_docs": 0.65,
        "chat": 0.95, "trivial": 0.99,
    },
    "codex": {
        "research": 0.7, "plan": 0.75, "implement": 0.95,
        "debug": 0.85, "review": 0.85, "write_docs": 0.7,
        "chat": 0.3, "trivial": 0.1,     # wasteful for trivia
    },
    "agy": {
        "research": 0.9, "plan": 0.95, "implement": 0.7,
        "debug": 0.75, "review": 0.8, "write_docs": 0.85,
        "chat": 0.3, "trivial": 0.1,
    },
    "hermes": {
        "research": 0.85, "plan": 0.6, "implement": 0.5,
        "debug": 0.7, "review": 0.75, "write_docs": 0.8,
        "chat": 0.6, "trivial": 0.2,
    },
    "opencode": {
        "research": 0.6, "plan": 0.55, "implement": 0.8,
        "debug": 0.7, "review": 0.65, "write_docs": 0.6,
        "chat": 0.3, "trivial": 0.15,
    },
    "server": {       # aikod — routes to its own agents; server-side work
        "research": 0.8, "plan": 0.75, "implement": 0.85,
        "debug": 0.8, "review": 0.75, "write_docs": 0.8,
        "chat": 0.2, "trivial": 0.05,     # never ship a haiku to a server
    },
}

TRIVIAL_MARKERS = (
    "haiku", "one sentence", "a sentence", "one word", "short answer",
    "quick question", "tiny", "summarize in", "in one line", "just say",
    "say hi", "what is 2+2", "name a ", "give me a name", "title for",
    "brainstorm a few", "list a few", "briefly",
)


def load_caps(path: Path | None = None) -> dict:
    p = path or CAPS_PATH
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.dump({"tools": DEFAULT_CAPS}, sort_keys=False))
    return yaml.safe_load(p.read_text()) or {}


def infer_task_shape(text: str) -> str:
    """Cheap shape gate: trivial vs standard types vs chat."""
    t = text.lower().strip()
    if any(m in t for m in TRIVIAL_MARKERS):
        return "trivial"
    if len(t) < 200 and not any(w in t for w in (
            "build", "implement", "refactor", "debug", "fix", "research",
            "investigate", "document", "write docs", "review", "audit")):
        return "chat"
    # reuse the daemon's keyword heuristics for standard types
    try:
        from ..aikod.scheduler import infer_task_type
        return infer_task_type(text)
    except Exception:
        return "plan"


def classify(text: str, declared_type: str | None = None) -> dict:
    """Classify a request: shape + triviality + size hints."""
    shape = declared_type or infer_task_shape(text)
    trivial = shape == "trivial" or (shape == "chat" and len(text) < 120)
    return {
        "shape": shape,
        "trivial": trivial,
        "chars": len(text),
        "needs_code": bool(re.search(
            r"\b(code|function|bug|error|stack trace|compile|test file|repo|"
            r"refactor|implement)\b", text, re.I)),
        "needs_files": bool(re.search(
            r"\b(file|directory|path|~/|\.py|\.ts|\.tsx|\.go|\.md|config)\b",
            text, re.I)),
        "needs_search": bool(re.search(
            r"\b(research|find out|compare|look up|latest|docs for)\b",
            text, re.I)),
    }


def availability(tool: str) -> float:
    """0-1 from the usage economy + health. Unknown tool = 0.8 neutral."""
    if tool == "self":
        return 1.0        # her own brain: budgets checked at brain level
    try:
        from .usage import exhaustion
        e = exhaustion(tool)
        return 1.0 if e is None else max(0.0, e)
    except Exception:
        return 0.8


def rank_tools(task_shape: str, usage_snapshot=None) -> list[dict]:
    """Ranked candidates for a task shape: tool, score, reasoning, why.

    Score = capability_floor × usage_availability + learned_modifier.
    The modifier is the feedback loop: real outcomes drift the ranking
    (promote proven tools, demote flaky ones) without any token spend.
    """
    caps = load_caps().get("tools", DEFAULT_CAPS)
    rows = []
    for tool, floors in caps.items():
        floor = floors.get(task_shape, 0.4)
        avail = availability(tool)
        if usage_snapshot and tool in usage_snapshot:
            # external snapshot (e.g. from the daemon) can override
            avail = usage_snapshot[tool]
        try:
            from .feedback import modifier_for
            fb = modifier_for(tool, task_shape)
        except Exception:
            fb = 0.0
        score = round(max(0.0, min(1.0, floor * avail + fb)), 3)
        if score <= 0:
            continue
        why = f"floor={floor} × availability={avail:.2f}"
        if fb:
            why += f" {'+' if fb > 0 else ''}{fb} learned"
        why += f" → {score}"
        rows.append({
            "tool": tool, "score": score,
            "floor": floor, "availability": avail, "feedback": fb,
            "reasoning": why,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def recommend(text: str, declared_type: str | None = None) -> dict:
    """The full recommendation: what to do, how, and the ranked table.

    Trivial → Aiko answers herself (her default brain, low reasoning —
    no need to burn high-reasoning tokens on a haiku).
    Otherwise → top-ranked tool wins; dispatch specs include the table.
    When the best tool is 'self', a per-model ranking + ε-greedy
    exploration picks WHICH brain: proven models stay, under-tried
    models get sampled on low-stakes tasks so learning can start.
    """
    c = classify(text, declared_type)
    if c["trivial"]:
        return {
            "action": "answer_self", "reasoning_level": "low",
            "classification": c,
            "ranked": rank_tools("trivial"),
            "why": "trivial task — Aiko answers directly, reasoning=low, "
                   "zero dispatch, zero worker tokens",
        }
    ranked = rank_tools(c["shape"])
    best = ranked[0] if ranked else None
    rec = {
        "action": "dispatch" if best and best["tool"] != "self" else "answer_self",
        "tool": best["tool"] if best else "self",
        "classification": c,
        "ranked": ranked,
        "why": best["reasoning"] if best else "no tools available",
    }
    if rec["action"] == "answer_self" and not c["trivial"]:
        pick = pick_brain(c["shape"], explore=True)
        if pick:
            rec["brain"] = pick["model"]
            rec["brain_why"] = pick["why"]
            rec["brain_score"] = pick["score"]
    return rec


# ── model-level selection + exploration ─────────────────────────

import random

EXPLORE_RATE = 0.15      # 15% of low-stakes self-answers sample a new model
EXPLORE_TOP = 6          # candidates worth exploring (not deep-tail junk)


def pick_brain(task_shape: str, explore: bool = True) -> dict | None:
    """Choose WHICH brain for a self-answered task.

    ε-greedy: 85% take the top-ranked model, 15% sample one of the top
    EXPLORE_TOP — the feedback ledger learns from the outcome either way.
    Trivial/chat shapes explore freely (low stakes); heavy shapes stick
    to proven models unless they're untried anyway.
    """
    from .models_catalog import rank_models
    ranked = rank_models(task_shape, limit=EXPLORE_TOP)
    if not ranked:
        return None
    low_stakes = task_shape in ("trivial", "chat")
    if explore and low_stakes and random.random() < EXPLORE_RATE:
        choice = random.choice(ranked)
        return {**choice, "why": f"exploring: {choice['why']} "
                                 f"(ε={EXPLORE_RATE:.0%} sample, learns from outcome)"}
    return ranked[0]


# ── prompt + tool exposure for the orchestrator brain ──────────

def recommendation_block(text: str) -> str:
    """A compact block injected into her system prompt per message:
    classification + ranked table so her dispatch decisions are informed."""
    r = recommend(text)
    lines = [f"TOOL SELECTION for this message: {r['action']}"
             + (f" → {r['tool']}" if r.get("tool") else "")
             + f" ({r['why'][:90]})"]
    if r.get("ranked"):
        top = ", ".join(f"{x['tool']}={x['score']:.2f}" for x in r["ranked"][:4])
        lines.append(f"ranked: {top}")
    return "\n".join(lines)
