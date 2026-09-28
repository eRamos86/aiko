"""Durable worker panes, independent of the daemon's lifetime.

The service must use KillMode=process and stop only the daemon. A named
socket alone does not move children out of a systemd control group.
"""
import re
import shlex
import subprocess
from pathlib import Path

SOCKET = "aiko-workers"
LEGACY_SOCKET = "default"


def command(socket=SOCKET):
    # Explicit -L default avoids inheriting a caller's $TMUX session.
    return ["tmux", "-L", socket]


def name(sid):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise ValueError("invalid worker session id")
    return f"aiko-{sid}"


def inspect(sid, socket=None):
    """Return present/alive/exit_code; transport errors are never absence."""
    target = "=" + name(sid)
    sockets = [socket] if socket else [SOCKET, LEGACY_SOCKET]
    for candidate in sockets:
        r = subprocess.run(command(candidate) + ["list-panes", "-t", target,
                           "-F", "#{pane_dead}|#{pane_dead_status}"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode:
            # tmux distinguishes missing sessions/servers from other failures.
            if any(s in r.stderr.lower() for s in (
                    "can't find", "no server running", "no such file or directory")):
                continue
            raise RuntimeError(f"tmux probe failed on {candidate}: {r.stderr.strip()}")
        lines = r.stdout.strip().splitlines()
        if len(lines) != 1 or "|" not in lines[0]:
            raise RuntimeError("unexpected worker pane layout")
        dead, status = lines[0].split("|", 1)
        if dead not in ("0", "1"):
            raise RuntimeError("invalid tmux pane state")
        return {"present": True, "alive": dead == "0", "socket": candidate,
                "exit_code": int(status) if dead == "1" and status.isdigit() else None}
    return {"present": False, "alive": False, "socket": socket, "exit_code": None}


def spawn(sid, argv, transcript, worktree):
    """Unique tmux name plus retained pane prevents repeat launch after a crash."""
    worker_name = name(sid)
    transcript = Path(transcript)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    # Configure retention *inside* the pane, before executing any worker. This
    # closes the fast-exit race in new-session followed by set-option.
    retain = shlex.join(command() + ["set-option", "-w", "-t", "=" + worker_name,
                                    "remain-on-exit", "on"])
    pipeline = shlex.join(argv) + " 2>&1 | tee -a " + shlex.quote(str(transcript))
    script = retain + " && exec " + shlex.join(["bash", "-o", "pipefail", "-c", pipeline])
    subprocess.run(command() + ["new-session", "-d", "-s", worker_name,
                               "-c", str(worktree), shlex.join(["sh", "-c", script])],
                   check=True, capture_output=True, text=True, timeout=15)


def send(sid, text, socket=None):
    state = inspect(sid, socket)
    if not state["alive"]:
        raise RuntimeError("worker is not alive")
    prefix = command(state["socket"]) + ["send-keys", "-t", "=" + name(sid)]
    subprocess.run(prefix + ["-l", "--", text], check=True, timeout=5)
    subprocess.run(prefix + ["Enter"], check=True, timeout=5)


def kill(sid):
    state = inspect(sid)
    if state["present"]:
        subprocess.run(command(state["socket"]) + ["kill-session", "-t", "=" + name(sid)],
                       check=True, timeout=5)


class TmuxAdapter:
    def session_metadata(self, task):
        sid = task["id"]
        name(sid)
        return {"transcript_path": str(Path.home() / ".aikod" / "transcripts" / f"{sid}.{self.manifest.provider_id}.log"),
                "worktree_path": str(Path(task.get("worktree_path") or "/tmp/aikod").resolve()),
                "tmux_socket": SOCKET}

    def status(self, session_id):
        return inspect(session_id)

    def send_input(self, session_id, text):
        send(session_id, text)

    def kill(self, session_id):
        kill(session_id)

    def launch(self, task, bundle_path, argv):
        metadata = self.session_metadata(task)
        metadata.update({k: task[k] for k in metadata if task.get(k) is not None})
        Path(metadata["worktree_path"]).mkdir(parents=True, exist_ok=True)
        prompt = task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text()
        spawn(task["id"], argv + [prompt], metadata["transcript_path"], metadata["worktree_path"])
        return task["id"]
