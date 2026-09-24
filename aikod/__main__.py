import argparse
import os
import threading
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .context import assemble_bundle
from .db import connect
from .events import append
from .scheduler import tick
from .supervisor import adopt_on_startup, heartbeat_loop


def spawn_routed_task(db_path, adapters, task_id, decision):
    """Hand the routed task to its chosen adapter: assemble bundle, spawn, record."""
    from pathlib import Path
    conn = connect(Path(db_path))
    row = conn.execute("SELECT spec, title FROM task WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    spec, title = row
    adapter = adapters[decision.provider_id]

    bundle_path = Path(f"/tmp/aikod/{task_id}.bundle.md")
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(assemble_bundle(spec))

    session_id = adapter.spawn({"id": task_id, "spec": spec, "model": decision.model},
                               str(bundle_path))
    # usage economy: every worker spawn counts against its rolling window
    try:
        from .usage import note_spawn
        note_spawn(decision.provider_id, decision.model)
    except Exception:
        pass
    conn.execute(
        "INSERT INTO session(id, task_id, provider_id, model, state) VALUES (?,?,?,?, 'live')",
        (session_id, task_id, decision.provider_id, decision.model),
    )
    append(conn, session_id, "session.spawned", {
        "provider": decision.provider_id, "model": decision.model,
    })
    return session_id


def orchestrator_loop(db_path, adapters, poll_interval: float = 5.0):
    """Server-side orchestrator (ADR-012): watch running tasks; when a
    worker finishes, decide follow-up work ON THE SERVER — spawn subtasks,
    steer workers, reply to the goal — no round-trip to the local client.

    v1 trigger: any task reaching 'running' gets one orchestrator pass
    when its session exits; the orchestrator may queue subtasks (staying
    on this server) or mark the goal complete with a summary reply."""
    import time as _time
    from .orchestrator import ServerOrchestrator

    seen: set[str] = set()
    while True:
        try:
            conn = connect(Path(db_path))
            rows = conn.execute(
                "SELECT s.task_id FROM session s WHERE s.state='exited' "
                "AND s.task_id NOT IN (SELECT task_id FROM orchestrator_run)"
            ).fetchall() if _orchestrator_runs_table(conn) else []
            for (task_id,) in rows:
                if task_id in seen:
                    continue
                seen.add(task_id)
                try:
                    orch = ServerOrchestrator(db_path, task_id)
                    reply = orch.step(
                        "The worker session for this task has exited. Read the "
                        "transcript with your tools, then decide: if the task "
                        "is done, reply with a short completion summary for "
                        "the goal. If more work is needed, dispatch subtasks "
                        "on this server. Do not ask the user questions.")
                    # usage economy: scan for limit-exceeded phrases → cooldown
                    try:
                        from .usage import in_cooldown, note_transcript_limits
                        conn3 = connect(Path(db_path))
                        sess_row = conn3.execute(
                            "SELECT provider_id FROM session WHERE task_id=?",
                            (task_id,)).fetchone()
                        if sess_row and in_cooldown(sess_row[0]) <= 0:
                            from pathlib import Path as _P
                            for cand in (_P.home() / ".aikod" / "transcripts").glob(f"{task_id}*"):
                                if cand.is_file():
                                    note_transcript_limits(sess_row[0],
                                                           cand.read_text(errors="replace"))
                                    break
                    except Exception:
                        pass
                    conn2 = connect(Path(db_path))
                    conn2.execute(
                        "INSERT INTO orchestrator_run(task_id, reply, ts) "
                        "VALUES (?,?,datetime('now'))", (task_id, reply))
                    conn2.execute(
                        "UPDATE task SET state='completed' WHERE id=? AND state='running'",
                        (task_id,))
                    append(conn2, task_id, "task.orchestrated",
                           {"reply": reply[:2000]})
                    # learning: fold this outcome into the router modifiers
                    try:
                        from .learning import learn_observed
                        delta = learn_observed(db_path, task_id, reply)
                        if delta:
                            append(conn2, task_id, "task.learned", delta)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[orchestrator] {task_id}: {e}", flush=True)
        except Exception as e:
            print(f"[orchestrator] loop error: {e}", flush=True)
        _time.sleep(poll_interval)


def _orchestrator_runs_table(conn) -> bool:
    """Ensure the orchestrator_run ledger exists (idempotent)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS orchestrator_run ("
        "task_id TEXT PRIMARY KEY, reply TEXT, ts TEXT)")
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.path.expanduser("~/.aikod/aikod.db"))
    p.add_argument("--bind", default=os.environ.get("ACED_BIND", "127.0.0.1:4090"))
    p.add_argument("--scheduler-interval", type=int, default=10)
    args = p.parse_args()

    from .adapters.registry import ADAPTERS

    adopt_on_startup(args.db)

    threading.Thread(target=heartbeat_loop, args=(args.db,), daemon=True).start()

    def scheduler_loop():
        while True:
            try:
                routed = tick(args.db, adapters=ADAPTERS)
                if routed:
                    task_id, decision = routed
                    spawn_routed_task(args.db, ADAPTERS, task_id, decision)
            except Exception as e:
                print(f"[scheduler] tick error: {e}", flush=True)
            time.sleep(args.scheduler_interval)

    threading.Thread(target=scheduler_loop, daemon=True).start()
    if os.environ.get("AIKOD_ORCHESTRATOR", "1") != "0":
        threading.Thread(target=orchestrator_loop,
                         args=(args.db, ADAPTERS), daemon=True).start()

    app = create_app(args.db)
    host, port = args.bind.split(":")
    uvicorn.run(app, host=host, port=int(port))


if __name__ == "__main__":
    main()
