"""Restart an owned Linux daemon through systemd's existing Restart=always policy.

The administrator installs the service once with KillMode=process. Updates then
signal only the validated daemon PID, preserving every worker process. There is
no sudoers rule, arbitrary PID input, or terminal-session reconstruction.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen


def service_state(service: str) -> dict:
    result = subprocess.run(["systemctl", "show", service, "--property=MainPID,KillMode,Restart"],
                            text=True, capture_output=True, check=True, timeout=10)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def restart(service="aikod.service", db_path=None, health_url="http://127.0.0.1:4090/health",
            timeout=60):
    from aikod.control import update_status
    state = service_state(service)
    if state.get("KillMode") != "process" or state.get("Restart") != "always":
        raise RuntimeError("Install the Aiko service with KillMode=process and Restart=always before updating")
    pid = int(state.get("MainPID", "0"))
    if pid <= 1:
        raise RuntimeError("Aiko service is not running; an administrator must start it")
    proc = Path("/proc") / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise RuntimeError("Aiko daemon is not owned by the current user")
    argv = proc.joinpath("cmdline").read_bytes().decode().split("\0")
    if not any(arg == "aikod" or Path(arg).name == "aikod" for arg in argv):
        raise RuntimeError("Service MainPID is not an Aiko daemon")
    db_path = Path(db_path or Path.home() / ".aikod/aikod.db")
    deadline = time.monotonic() + timeout
    try:
        status = update_status(db_path, pause=True)
        while not status["safe_to_restart"]:
            if time.monotonic() >= deadline:
                raise RuntimeError("Aiko is finishing an orchestration step; retry the update shortly")
            time.sleep(0.25)
            status = update_status(db_path)
        if int(service_state(service).get("MainPID", "0")) != pid:
            raise RuntimeError("Daemon changed during update; retry after it settles")
        os.kill(pid, signal.SIGTERM)
        while time.monotonic() < deadline:
            new_pid = int(service_state(service).get("MainPID", "0"))
            if new_pid > 1 and new_pid != pid:
                try:
                    with urlopen(health_url, timeout=3) as response:
                        health = json.load(response)
                    expected = Path(sys.prefix).resolve().parent
                    runtime = Path(health.get("runtime", "/missing")).resolve()
                    if health.get("status") == "ok" and runtime.is_relative_to(expected):
                        update_status(db_path, pause=False)
                        return {"status": "restarted", "previous_pid": pid, "pid": new_pid}
                except (OSError, ValueError):
                    pass
            time.sleep(0.5)
        raise RuntimeError("New daemon did not become healthy within the restart window")
    finally:
        # On a failed preflight the existing scheduler must not remain paused.
        # On failure after signalling, resume is also safe: either the new daemon
        # owns the DB or the installer will restore/restart the previous release.
        update_status(db_path, pause=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["restart"])
    parser.add_argument("--service", default="aikod.service")
    parser.add_argument("--db")
    parser.add_argument("--health-url", default="http://127.0.0.1:4090/health")
    args = parser.parse_args()
    print(json.dumps(restart(args.service, args.db, args.health_url)))


if __name__ == "__main__":
    main()
