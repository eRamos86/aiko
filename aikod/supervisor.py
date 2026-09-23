"""Tmux pane lifecycle. On daemon start, walk all `aiko-*` tmux sessions
and re-adopt them (heartbeat-based reconciliation per Aiko §10)."""
import subprocess
import time
from pathlib import Path

from .events import append
from .db import connect

SUPERVISOR_INTERVAL = 30  # seconds between heartbeats


def list_aced_sessions() -> list[str]:
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return [name for name in r.stdout.splitlines() if name.startswith("aiko-")]


def adopt_on_startup(db_path):
    """Called from daemon lifespan startup."""
    conn = connect(Path(db_path))
    for tmux_name in list_aced_sessions():
        session_id = tmux_name[len("aiko-"):]
        append(conn, session_id, "supervisor.adopted", {"tmux_session": tmux_name})
    return len(list_aced_sessions())


def heartbeat_once(db_path) -> dict:
    """One reconciliation pass. Returns summary for tests/callers."""
    conn = connect(Path(db_path))
    live_tmux = set(list_aced_sessions())
    exited = []
    cur = conn.execute("SELECT id FROM session WHERE state='live'")
    for (sid,) in cur.fetchall():
        tmux_name = f"aiko-{sid}"
        alive = tmux_name in live_tmux
        append(conn, sid, "supervisor.heartbeat", {"alive": alive})
        if not alive:
            conn.execute("UPDATE session SET state='exited' WHERE id=?", (sid,))
            append(conn, sid, "session.exited", {})
            exited.append(sid)
    return {"live": sorted(live_tmux), "exited": exited}


def heartbeat_loop(db_path, interval=SUPERVISOR_INTERVAL):
    while True:
        try:
            heartbeat_once(db_path)
        except Exception:
            pass  # supervisor must never die
        time.sleep(interval)
