"""Atomic routing and capacity reservation; external checks run outside transactions."""
import json
from .db import database, transaction
from .control import paused
from .events import append
from .router import choose


def tick(db_path, adapters=None, log=True):
    from .adapters.registry import ADAPTERS
    pool = adapters if adapters is not None else ADAPTERS
    with database(db_path) as conn:
        if paused(conn):
            return None
        rows = conn.execute(
            "SELECT t.id,t.title,t.spec,g.locality FROM task t JOIN goal g ON g.id=t.goal_id "
            "WHERE t.state='ready' AND t.assigned_provider IS NULL ORDER BY t.created_at,t.id"
        ).fetchall()
        counts = dict(conn.execute("SELECT provider_id,COUNT(*) FROM session "
                                  "WHERE state IN ('pending','spawning','live') GROUP BY provider_id"))
        available = {k: v for k, v in pool.items()
                     if counts.get(k, 0) < v.manifest.concurrency_limit}
        if not available:
            return None
        for task_id, title, spec, locality in rows:
            try:
                decision = choose(infer_task_type(title + "\n" + spec), task_id,
                                  locality=locality, adapters=available, log=False)
            except RuntimeError:
                continue  # unroutable work must not block independent tasks
            provider = decision.provider_id
            if provider not in available:
                continue
            adapter = available[provider]
            metadata = (adapter.session_metadata({"id": task_id})
                        if hasattr(adapter, "session_metadata") else {})
            with transaction(conn):
                count = conn.execute("SELECT COUNT(*) FROM session WHERE provider_id=? "
                                     "AND state IN ('pending','spawning','live')", (provider,)).fetchone()[0]
                if paused(conn) or count >= adapter.manifest.concurrency_limit:
                    return None
                changed = conn.execute(
                    "UPDATE task SET state='running',assigned_provider=?,assigned_model=? "
                    "WHERE id=? AND state='ready' AND assigned_provider IS NULL",
                    (provider, decision.model, task_id)).rowcount
                if not changed:
                    continue
                conn.execute("INSERT INTO session(id,task_id,provider_id,model,state,"
                             "transcript_path,worktree_path,tmux_socket) VALUES (?,?,?,?,'pending',?,?,?)",
                             (task_id, task_id, provider, decision.model, metadata.get("transcript_path"),
                              metadata.get("worktree_path"), metadata.get("tmux_socket")))
                conn.execute("INSERT INTO routing_decision(task_id,chosen_provider,chosen_model,"
                             "yaml_scores_json,observed_weights_json,reasoning,ts) "
                             "VALUES (?,?,?,?,?,?,datetime('now'))",
                             (task_id, provider, decision.model,
                              json.dumps({"chosen": decision.yaml_score, "candidates": decision.candidates}),
                              json.dumps({"chosen": decision.observed_modifier}), decision.reasoning))
                append(conn, task_id, "task.routed", {
                    "provider": provider, "model": decision.model,
                    "final_score": decision.final_score, "reasoning": decision.reasoning})
            if log:
                try:
                    from .routing_log import write_decision
                    write_decision(task_id, decision)
                except OSError:
                    pass  # DB audit is authoritative
            return task_id, decision
    return None


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
