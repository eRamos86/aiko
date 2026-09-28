"""Persistent update barrier and single-daemon ownership (Unix)."""
import fcntl
from pathlib import Path

from .db import database, transaction


def daemon_lock(db_path):
    """Keep the returned handle open for the daemon lifetime. Never unlink it."""
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.with_suffix(path.suffix + ".daemon.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("another daemon owns this database") from None
    return handle


def paused(conn):
    return bool(conn.execute("SELECT paused FROM daemon_control WHERE id=1").fetchone()[0])


def update_status(db_path, pause=None):
    with database(db_path) as conn, transaction(conn):
        if pause is not None:
            conn.execute("UPDATE daemon_control SET paused=? WHERE id=1", (int(pause),))
        spawning = conn.execute("SELECT COUNT(*) FROM session WHERE state='spawning'").fetchone()[0]
        orchestrating = conn.execute("SELECT COUNT(*) FROM orchestrator_job WHERE state='running'").fetchone()[0]
        is_paused = paused(conn)
        return {"paused": is_paused, "spawning": spawning,
                "orchestrating": orchestrating,
                "safe_to_restart": is_paused and not spawning and not orchestrating}
