"""Reconcile recorded workers; retain dead panes as exit evidence."""
import logging
import time
from pathlib import Path

from .adapters import tmux
from .db import database, transaction
from .events import append

SUPERVISOR_INTERVAL = 5


def send_to_session(db_path, sid, text, adapters=None):
    """Control only database-owned sessions, never arbitrary tmux names."""
    if adapters is None:
        from .adapters.registry import ADAPTERS
        adapters = ADAPTERS
    with database(db_path) as conn:
        row = conn.execute("SELECT provider_id,tmux_socket,state FROM session WHERE id=?", (sid,)).fetchone()
    if not row or row[2] != "live":
        raise RuntimeError("no live recorded session")
    adapter = adapters.get(row[0])
    if row[0] in ("codex", "hermes") or (adapter and adapter.manifest.control_daemon == "tmux-bridge"):
        tmux.send(sid, text, row[1])
    elif adapter:
        adapter.send_input(sid, text)
    else:
        raise RuntimeError("session adapter unavailable")


def transcript_path(conn, sid, root=None):
    row = conn.execute("SELECT transcript_path FROM session WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    if row[0]:
        path = Path(row[0])
        return path if path.is_file() else None
    tmux.name(sid)
    root = root or Path.home() / ".aikod" / "transcripts"
    # Exact legacy suffixes, never a user-controlled glob.
    for suffix in (".codex.jsonl", ".jsonl", ".log", ".codex.log", ".hermes.log"):
        path = root / (sid + suffix)
        if path.is_file():
            conn.execute("UPDATE session SET transcript_path=? WHERE id=?", (str(path), sid))
            return path
    return None


def adopt_on_startup(db_path, adapters=None):
    """Run under the exclusive daemon lock. Uncertain launches are not replayed."""
    with database(db_path) as conn, transaction(conn):
        conn.execute("UPDATE orchestrator_job SET state='pending' WHERE state='running'")
        rows = conn.execute("SELECT id FROM task WHERE state='running' "
                            "AND NOT EXISTS (SELECT 1 FROM session WHERE task_id=task.id)").fetchall()
        for (tid,) in rows:
            conn.execute("UPDATE task SET state='failed' WHERE id=?", (tid,))
            append(conn, tid, "task.recovery_required", {"reason": "legacy task has no recorded session"})
    return heartbeat_once(db_path, adapters, startup=True)


def heartbeat_once(db_path, adapters=None, startup=False):
    if adapters is None:
        from .adapters.registry import ADAPTERS
        adapters = ADAPTERS
    live, exited, errors = [], [], []
    with database(db_path) as conn:
        states = "('live','spawning')" if startup else "('live')"
        rows = conn.execute(f"SELECT id,task_id,provider_id,tmux_socket,state FROM session WHERE state IN {states}").fetchall()
        for sid, tid, provider, socket, previous in rows:
            try:
                adapter = adapters.get(provider)
                if provider in ("codex", "hermes") or (adapter and adapter.manifest.control_daemon == "tmux-bridge"):
                    status = tmux.inspect(sid, socket)
                elif previous == "spawning":
                    # Native bridge has no task-id idempotency or discovery contract.
                    status = {"alive": False, "error": "native launch outcome unknown"}
                elif adapter:
                    status = adapter.status(sid)
                else:
                    raise RuntimeError(f"adapter unavailable: {provider}")
                if status.get("unknown"):
                    raise RuntimeError("worker status unavailable")
                with transaction(conn):
                    current = conn.execute("SELECT state FROM session WHERE id=?", (sid,)).fetchone()[0]
                    if current not in ("live", "spawning"):
                        continue
                    if status.get("alive"):
                        conn.execute("UPDATE session SET state='live',tmux_socket=COALESCE(?,tmux_socket) WHERE id=?",
                                     (status.get("socket"), sid))
                        if startup:
                            append(conn, sid, "supervisor.adopted", {"socket": status.get("socket")})
                        live.append(sid)
                    else:
                        code = status.get("exit_code")
                        error = None if code == 0 else status.get("error") or (
                            f"worker exit {code}" if code is not None else "worker exit status unknown")
                        conn.execute("UPDATE session SET state='exited',exit_code=?,error=?,"
                                     "tmux_socket=COALESCE(?,tmux_socket) WHERE id=?",
                                     (code, error, status.get("socket"), sid))
                        if error:
                            conn.execute("UPDATE task SET state='failed' WHERE id=? AND state='running'", (tid,))
                        append(conn, sid, "session.exited", {"exit_code": code, "error": error})
                        exited.append(sid)
                    transcript_path(conn, sid)
            except Exception as exc:
                errors.append(sid)
                logging.getLogger(__name__).warning("worker probe %s failed: %s", sid, exc)
    return {"live": live, "exited": exited, "errors": errors}


def heartbeat_loop(db_path, interval=SUPERVISOR_INTERVAL, stop=None):
    while stop is None or not stop.is_set():
        try:
            heartbeat_once(db_path)
        except Exception:
            logging.getLogger(__name__).exception("supervisor pass failed")
        if stop is not None:
            stop.wait(interval)
        else:
            time.sleep(interval)
