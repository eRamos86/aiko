"""v0.1 scheduler: FIFO queue. Real DAG traversal lands in v0.4 per Aiko roadmap."""
import uuid
from pathlib import Path

from .db import connect
from .events import append
from .router import choose


def tick(db_path, adapters=None, log=True):
    """Route one ready task: pick provider+model, mark running, emit event.

    v0.1: spawn is triggered by the caller after routing (the daemon loop
    passes the decision to the chosen adapter). Returns (task_id, decision)
    or None.
    """
    conn = connect(Path(db_path))
    row = conn.execute(
        "SELECT id, title, spec, goal_id FROM task "
        "WHERE state='ready' AND assigned_provider IS NULL "
        "ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not row:
        return None
    task_id, title, spec, goal_id = row

    # Infer task type from spec heuristics for v0.1; the planner (v0.4) will set it explicitly.
    task_type = infer_task_type(title + "\n" + spec)

    decision = choose(task_type, task_id, locality="auto",
                       adapters=adapters, log=log)
    conn.execute(
        "UPDATE task SET state='running', assigned_provider=?, assigned_model=? WHERE id=?",
        (decision.provider_id, decision.model, task_id),
    )
    append(conn, task_id, "task.routed", {
        "provider": decision.provider_id, "model": decision.model,
        "final_score": decision.final_score, "reasoning": decision.reasoning,
    })
    return task_id, decision


def infer_task_type(text: str) -> str:
    """Cheap keyword heuristic for v0.1 routing."""
    t = text.lower()
    # order matters: more specific intents first
    if any(w in t for w in ("debug", "investigate", "why is", "error", "crash", "failing",
                            "bug", "broken", "doesn't work", "not working", "fix the", "fix my")):
        return "debug"
    if any(w in t for w in ("document", "docs", "readme", "update obsidian", "write_docs")):
        return "write_docs"
    if any(w in t for w in ("review", "audit", "inspect")):
        return "review"
    if any(w in t for w in ("research", "find out", "compare", "look up")):
        return "research"
    if any(w in t for w in ("implement", "build", "write code", "refactor", "fix", "add feature")):
        return "implement"
    return "plan"
