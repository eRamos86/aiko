import argparse
import json
import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn

from .api import create_app
from .context import assemble_bundle
from .control import daemon_lock, paused
from .db import database, transaction
from .events import append
from .scheduler import tick
from .supervisor import adopt_on_startup, heartbeat_loop

logger = logging.getLogger(__name__)


def spawn_routed_task(db_path, adapters, task_id, decision=None):
    """Claim durable spawn intent before external work; never replay live workers."""
    from .adapters import tmux
    with database(db_path) as conn:
        with transaction(conn):
            if paused(conn):
                return None
            row = conn.execute(
                "SELECT s.id,s.provider_id,s.model,s.state,s.transcript_path,s.worktree_path,s.tmux_socket,t.spec "
                "FROM session s JOIN task t ON t.id=s.task_id WHERE s.task_id=? AND t.state='running'",
                (task_id,)).fetchone()
            # `tick()` normally reserves the pending session.  Permit the
            # explicit spawn entry point to make that reservation too: this is
            # used by recovery/admin callers and avoids a second, divergent
            # spawn path.
            if row is None and decision is not None:
                provider = decision.provider_id
                model = decision.model
                adapter = adapters.get(provider)
                if adapter is None:
                    return None
                metadata = (adapter.session_metadata({"id": task_id})
                            if hasattr(adapter, "session_metadata") else {})
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO session(id,task_id,provider_id,model,state,transcript_path,worktree_path,tmux_socket) "
                    "VALUES (?,?,?,?, 'pending',?,?,?)",
                    (task_id, task_id, provider, model, metadata.get("transcript_path"),
                     metadata.get("worktree_path"), metadata.get("tmux_socket")),
                ).rowcount
                if not inserted:
                    return None
                conn.execute("UPDATE task SET assigned_provider=?,assigned_model=? WHERE id=? AND state='running'",
                             (provider, model, task_id))
                row = conn.execute(
                    "SELECT s.id,s.provider_id,s.model,s.state,s.transcript_path,s.worktree_path,s.tmux_socket,t.spec "
                    "FROM session s JOIN task t ON t.id=s.task_id WHERE s.task_id=? AND t.state='running'",
                    (task_id,)).fetchone()
            if not row or row[3] != "pending":
                return None
            sid, provider, model, _, transcript, worktree, socket, spec = row
            conn.execute("UPDATE session SET state='spawning',error=NULL WHERE id=?", (sid,))
        launched = False
        is_tmux = False
        actual_sid = None
        try:
            adapter = adapters[provider]
            is_tmux = adapter.manifest.control_daemon == "tmux-bridge"
            if is_tmux:
                state = tmux.inspect(sid, socket)
                if state["present"]:
                    # Covers a process death between tmux creation and DB acknowledgement.
                    conn.execute("UPDATE session SET state='live',tmux_socket=? WHERE id=?",
                                 (state["socket"], sid))
                    return sid
            bundle_path = Path(db_path).resolve().parent / "bundles" / f"{sid}.bundle.md"
            tmux.name(sid)  # validate before using the ID in a filename
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_text(assemble_bundle(spec))
            with transaction(conn):
                if paused(conn):
                    conn.execute("UPDATE session SET state='pending' WHERE id=?", (sid,))
                    return None
            task = {"id": sid, "spec": spec, "model": model,
                    "transcript_path": transcript, "worktree_path": worktree, "tmux_socket": socket}
            launched = True
            actual_sid = adapter.spawn(task, str(bundle_path))
            with transaction(conn):
                conn.execute("UPDATE session SET id=?,state='live',error=NULL WHERE id=?", (actual_sid, sid))
                append(conn, actual_sid, "session.spawned", {"provider": provider, "model": model})
            try:
                from .usage import note_spawn
                note_spawn(provider, model)
            except Exception:
                logger.exception("usage accounting failed for %s", actual_sid)
            return actual_sid
        except Exception as exc:
            # Retry only a known pre-launch failure or a failed tmux creation
            # whose absence is confirmed. Native timeouts are inherently ambiguous.
            retry = not launched
            if launched and is_tmux:
                try:
                    state = tmux.inspect(sid, socket)
                    if state["present"]:
                        conn.execute("UPDATE session SET state='live',tmux_socket=? WHERE id=?",
                                     (state["socket"], sid))
                        return sid
                    retry = isinstance(exc, subprocess.CalledProcessError)
                except Exception:
                    retry = False
            with transaction(conn):
                conn.execute("UPDATE session SET state=?,error=? WHERE id=?",
                             ("pending" if retry else "exited", str(exc), sid))
                if not retry:
                    conn.execute("UPDATE task SET state='failed' WHERE id=? AND state='running'", (task_id,))
                append(conn, sid, "session.spawn_failed", {"error": str(exc), "retryable": retry})
            logger.warning("spawn %s failed (retryable=%s): %s", sid, retry, exc)
            return None


def orchestrator_once(db_path, task_id=None, brain_factory=None):
    """Claim one follow-up; checkpoint failures and child waits for future passes."""
    from .orchestrator import Deferred, ServerOrchestrator
    with database(db_path) as conn:
        with transaction(conn):
            if paused(conn):
                return None
            conn.execute(
                "INSERT OR IGNORE INTO orchestrator_job(task_id) "
                "SELECT DISTINCT s.task_id FROM session s JOIN task t ON t.id=s.task_id "
                "WHERE s.state='exited' AND t.state IN ('running','failed') "
                "AND NOT EXISTS (SELECT 1 FROM orchestrator_run r WHERE r.task_id=s.task_id)")
            query = (
                "SELECT j.task_id,j.state,j.history FROM orchestrator_job j JOIN task t ON t.id=j.task_id "
                "WHERE j.state IN ('pending','waiting') AND j.retry_at<=? AND t.state IN ('running','failed') "
                "AND (j.state='pending' OR NOT EXISTS "
                "(SELECT 1 FROM task c WHERE c.parent_task_id=j.task_id "
                "AND c.state NOT IN ('completed','failed','cancelled')))")
            params = [time.time()]
            if task_id:
                query += " AND j.task_id=?"
                params.append(task_id)
            row = conn.execute(query + " ORDER BY j.retry_at,j.task_id LIMIT 1", params).fetchone()
            if not row:
                return None
            task_id, previous, history = row
            if previous == "waiting":
                children = conn.execute("SELECT id,state FROM task WHERE parent_task_id=?", (task_id,)).fetchall()
                messages = json.loads(history) if history else []
                messages.append({"role": "user", "content": "Children have settled: " + json.dumps(children) +
                                 ". Review their results and finish, or dispatch needed follow-up work."})
                conn.execute("UPDATE orchestrator_job SET history=? WHERE task_id=?",
                             (json.dumps(messages), task_id))
            conn.execute("UPDATE orchestrator_job SET state='running',error=NULL WHERE task_id=?", (task_id,))
        try:
            orch = ServerOrchestrator(db_path, task_id, brain_factory=brain_factory)
            reply = orch.step(
                "The worker session exited. Inspect its explicit exit status and transcript using your tools. "
                "Summarize the result or dispatch needed subtasks on this server. Do not call failed work successful.")
            with transaction(conn):
                active = conn.execute(
                    "SELECT COUNT(*) FROM task WHERE parent_task_id=? "
                    "AND state NOT IN ('completed','failed','cancelled')", (task_id,)).fetchone()[0]
                if active:
                    conn.execute("UPDATE orchestrator_job SET state='waiting',reply=? WHERE task_id=?", (reply, task_id))
                else:
                    # Only explicit zero exits and successful children permit completion.
                    failed = conn.execute(
                        "SELECT EXISTS(SELECT 1 FROM session WHERE task_id=? AND "
                        "(state!='exited' OR exit_code IS NULL OR exit_code!=0)) OR "
                        "EXISTS(SELECT 1 FROM task WHERE parent_task_id=? AND state!='completed')",
                        (task_id, task_id)).fetchone()[0]
                    conn.execute("UPDATE task SET state=? WHERE id=? AND state IN ('running','failed')",
                                 ("failed" if failed else "completed", task_id))
                    conn.execute("INSERT INTO orchestrator_run(task_id,reply,ts) VALUES (?,?,datetime('now')) "
                                 "ON CONFLICT(task_id) DO UPDATE SET reply=excluded.reply,ts=excluded.ts",
                                 (task_id, reply))
                    conn.execute("UPDATE orchestrator_job SET state='completed',reply=? WHERE task_id=?", (reply, task_id))
                append(conn, task_id, "task.orchestrated", {"reply": reply[:2000], "waiting_for_children": bool(active)})
            if not active:
                try:
                    from .learning import learn_observed
                    delta = learn_observed(db_path, task_id, reply)
                    if delta:
                        append(conn, task_id, "task.learned", delta)
                except Exception:
                    logger.exception("learning failed for %s", task_id)
            return task_id
        except Exception as exc:
            with transaction(conn):
                conn.execute("UPDATE orchestrator_job SET state='pending',error=?,retry_at=? WHERE task_id=?",
                             (None if isinstance(exc, Deferred) else str(exc), time.time() + 5, task_id))
            if not isinstance(exc, Deferred):
                logger.exception("follow-up failed for %s", task_id)
            return task_id


def orchestrator_loop(db_path, adapters=None, poll_interval=5.0, stop=None):
    # Bounded independent passes keep one slow brain from stopping other goals.
    stop = stop or threading.Event()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="aikod-followup") as pool:
        futures = set()
        while not stop.is_set():
            futures = {f for f in futures if not f.done()}
            if len(futures) < 4:
                futures.add(pool.submit(orchestrator_once, db_path))
            stop.wait(poll_interval)


def scheduler_loop(db_path, adapters, interval=10, stop=None):
    stop = stop or threading.Event()
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="aikod-spawn") as pool:
        futures = {}
        while not stop.is_set():
            try:
                futures = {tid: f for tid, f in futures.items() if not f.done()}
                tick(db_path, adapters=adapters)
                with database(db_path) as conn:
                    pending = conn.execute("SELECT task_id FROM session WHERE state='pending'").fetchall()
                for (tid,) in pending:
                    if tid not in futures and len(futures) < 8:
                        futures[tid] = pool.submit(spawn_routed_task, db_path, adapters, tid)
            except Exception:
                logger.exception("scheduler pass failed")
            stop.wait(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.expanduser("~/.aikod/aikod.db"))
    parser.add_argument("--bind", default=os.environ.get("AIKOD_BIND", os.environ.get("ACED_BIND", "127.0.0.1:4090")))
    parser.add_argument("--scheduler-interval", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    from .adapters.registry import ADAPTERS

    # This handle is intentionally held for the entire serving lifetime.
    with daemon_lock(args.db):
        adopt_on_startup(args.db, ADAPTERS)
        stop = threading.Event()
        threads = [
            threading.Thread(target=heartbeat_loop, args=(args.db,), kwargs={"stop": stop}, daemon=True),
            threading.Thread(target=scheduler_loop, args=(args.db, ADAPTERS, args.scheduler_interval),
                             kwargs={"stop": stop}, daemon=True),
        ]
        if os.environ.get("AIKOD_ORCHESTRATOR", "1") != "0":
            threads.append(threading.Thread(target=orchestrator_loop, args=(args.db, ADAPTERS),
                                            kwargs={"stop": stop}, daemon=True))
        for thread in threads:
            thread.start()
        app = create_app(args.db)
        host, port = args.bind.rsplit(":", 1)
        try:
            uvicorn.run(app, host=host, port=int(port))
        finally:
            stop.set()
            # Worker panes are deliberately never killed by daemon shutdown.
            for thread in threads:
                thread.join(timeout=1)


if __name__ == "__main__":
    main()
