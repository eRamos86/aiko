import argparse
import os
import threading
import time

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
    conn.execute(
        "INSERT INTO session(id, task_id, provider_id, model, state) VALUES (?,?,?,?, 'live')",
        (session_id, task_id, decision.provider_id, decision.model),
    )
    append(conn, session_id, "session.spawned", {
        "provider": decision.provider_id, "model": decision.model,
    })
    return session_id


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

    app = create_app(args.db)
    host, port = args.bind.split(":")
    uvicorn.run(app, host=host, port=int(port))


if __name__ == "__main__":
    main()
