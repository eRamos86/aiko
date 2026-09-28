"""Durable direct workers, independent of the client and of aikod.

Interactive callers select a directory and call ``open``. Orchestrators use
``dispatch`` (protected branches require explicit ``repo_direct=True``).
Attach using the returned argv in a terminal; C-b d detaches without stopping
the worker. The SQLite registry is local; remote logs and exit receipts stay
on the configured SSH host. No Aiko installation is needed on that host.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import time
import uuid


SOCKET_NAME = "aiko-workers"
TERMINAL_STATES = {"completed", "failed", "cancelled"}


class SessionManager:
    def __init__(self, config=None):
        if config is None:
            import yaml
            path = Path.home() / ".aiko" / "config.yaml"
            config = yaml.safe_load(path.read_text()) if path.exists() else {}
        self.config = config or {}
        self.agents = {a["name"]: a for a in self.config.get("agents", [])}
        self.servers = {s["name"]: s for s in self.config.get("servers", [])}
        self.root = Path.home() / ".aiko"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path = self.root / "sessions.db"
        # Set permissions before SQLite can create journals or write prompts.
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS direct_sessions (
                id TEXT PRIMARY KEY, data TEXT NOT NULL,
                outcome_recorded INTEGER NOT NULL DEFAULT 0
            )""")
        self.db_path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self, row):
        with self._db() as db:
            db.execute("""INSERT INTO direct_sessions(id, data) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                       (row["id"], json.dumps(row)))
        return row

    def _load(self, session_id):
        with self._db() as db:
            result = db.execute("SELECT data FROM direct_sessions WHERE id=?",
                                (session_id,)).fetchone()
        if result is None:
            raise ValueError(f"unknown session: {session_id}")
        return json.loads(result[0])

    def _host(self, target):
        if target == "local":
            return None
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", target):
            raise ValueError("invalid target name")
        host = self.servers.get(target, {}).get("ssh_host")
        if (not isinstance(host, str) or not re.fullmatch(
                r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?"
                r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*|\[[0-9A-Fa-f:]+\])", host)):
            raise ValueError(f"target {target!r} needs a configured ssh_host")
        return host

    def _argv(self, target, argv, interactive=False):
        host = self._host(target)
        if host is None:
            return argv
        options = ["-t"] if interactive else ["-o", "BatchMode=yes"]
        return ["ssh", *options, "-o", "ConnectTimeout=10", "--", host,
                shlex.join(argv)]

    def _run(self, target, argv, check=True):
        result = subprocess.run(self._argv(target, argv), capture_output=True,
                                text=True, errors="replace", timeout=30)
        # An SSH transport failure must never be interpreted as worker exit.
        if result.returncode == 255 and target != "local":
            raise RuntimeError(result.stderr.strip() or "SSH target unreachable")
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or f"{argv[0]} failed")
        return result

    @staticmethod
    def _tmux(*args):
        return ["tmux", "-L", SOCKET_NAME, "-f", "/dev/null", *args]

    def _check_host(self, row):
        if self._host(row["target"]) != row["ssh_host"]:
            raise ValueError("session SSH host differs from current configuration")

    def _cwd(self, cwd, target):
        cwd = os.fspath(cwd)
        if not cwd or "\x00" in cwd:
            raise ValueError("cwd must be a directory")
        if target == "local":
            path = Path(cwd).expanduser().resolve()
            if not path.is_dir():
                raise ValueError(f"directory does not exist: {path}")
            return str(path)
        # Expansion is explicit and limited to the remote user's home. Values
        # are positional shell arguments, never interpolated shell source.
        script = ('case "$1" in "~") set -- "$HOME";; '
                  r'"~/"*) set -- "$HOME/${1#\~/}";; esac; '
                  'cd -- "$1" && pwd -P')
        result = self._run(target, ["sh", "-c", script, "aiko", cwd], check=False)
        if result.returncode:
            raise ValueError(f"remote directory does not exist: {cwd}")
        return result.stdout.rstrip("\n")

    def _agent(self, agent):
        if agent not in self.agents:
            raise ValueError(f"unknown agent: {agent}")
        cfg = self.agents[agent]
        if not isinstance(cfg.get("command"), str) or not cfg["command"]:
            raise ValueError(f"agent {agent!r} needs a command")
        return cfg

    @staticmethod
    def _command(argv, prompt=None):
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)):
            raise ValueError("agent command must be a nonempty argv list")
        if prompt is None:
            return argv.copy()
        if not isinstance(prompt, str) or "\x00" in prompt:
            raise ValueError("prompt must be text without NUL bytes")
        has_placeholder = any("{prompt}" in arg for arg in argv)
        result = [arg.replace("{prompt}", prompt) for arg in argv]
        return result if has_placeholder else [*result, prompt]

    def open(self, agent, cwd, target="local", prompt=None) -> dict:
        """Start interactive argv (default [command]) in a user-chosen cwd.

        Providing cwd here represents the interactive user's repository choice.
        Automated callers must use dispatch(), which enforces branch safety.
        ``interactive`` is a complete argv; a prompt replaces {prompt}, or is
        appended as a single argument when no placeholder is present.
        """
        cfg = self._agent(agent)
        argv = self._command(cfg.get("interactive", [cfg["command"]]), prompt)
        return self._start(agent, cwd, target, argv, prompt, "interactive")

    def dispatch(self, agent, cwd, prompt, target="local", *, repo_direct=False):
        """Run configured one_shot arguments under tmux, with branch protection."""
        cfg = self._agent(agent)
        self._host(target)
        cwd = self._cwd(cwd, target)
        if repo_direct is not True:
            result = self._run(target, ["git", "-C", cwd, "symbolic-ref", "--short", "HEAD"],
                               check=False)
            if result.returncode and not (result.returncode == 1 or
                    "not a git repository" in result.stderr.lower()):
                raise RuntimeError(result.stderr.strip() or "cannot verify repository branch")
            protected = {"main", "master", *self.config.get("protected_branches", [])}
            if result.stdout.strip() in protected:
                raise ValueError("automated dispatch on a protected branch requires an "
                                 "isolated worktree or explicit repo_direct=True")
        template = cfg.get("one_shot", ["{prompt}"])
        argv = [cfg["command"], *self._command(template, prompt)]
        return self._start(agent, cwd, target, argv, prompt, "one_shot")

    def _start(self, agent, cwd, target, argv, prompt, mode):
        host = self._host(target)
        cwd = self._cwd(cwd, target)
        token = uuid.uuid4().hex
        sid = f"{target}-{token}"
        name = f"aiko-{token}"
        if host is None:
            directory = self.root / "sessions" / token
            directory.mkdir(mode=0o700, parents=True)
            directory = str(directory)
        else:
            home = self._run(target, ["sh", "-c", 'printf "%s" "$HOME"']).stdout
            if not home.startswith("/") or "\x00" in home:
                raise RuntimeError("SSH target did not return an absolute home directory")
            directory = f"{home}/.aiko/sessions/{token}"
            self._run(target, ["mkdir", "-p", "-m", "700", directory])
        log = f"{directory}/transcript.log"
        receipt = f"{directory}/exit-code"
        row = dict(id=sid, session_id=sid, target=target, ssh_host=host,
                   agent=agent, provider=agent, cwd=cwd, command=argv, mode=mode,
                   title=(prompt or agent)[:200], tmux_session=name,
                   transcript=log, log_path=log, exit_path=receipt,
                   state="starting", session_state="starting", exit_code=None,
                   started=time.time(), updated_at=time.time(), error=None)
        self._save(row)
        # The barrier prevents fast workers from exiting before logging and
        # remain-on-exit are installed. All setup runs in one tmux command queue.
        barrier = f"{name}-ready"
        script = (shlex.join(self._tmux("wait-for", barrier)) + ' || exit 125; '
                  'umask 077; receipt=$1; workdir=$2; shift 2; '
                  'if cd -- "$workdir"; then "$@"; code=$?; else code=$?; fi; '
                  'printf "%s\\n" "$code" > "$receipt.tmp" && '
                  'mv -f "$receipt.tmp" "$receipt"; exit "$code"')
        worker = shlex.join(["sh", "-c", script, "aiko-worker", receipt, cwd, *argv])
        pipe = shlex.join(["sh", "-c", 'umask 077; cat >> "$1"', "aiko-log", log])
        cmd = self._tmux(
            "new-session", "-d", "-s", name, "-c", cwd.replace("#", "##"), worker, ";",
            "set-option", "-t", name, "@aiko_agent", agent, ";",
            "set-option", "-t", name, "destroy-unattached", "off", ";",
            "set-option", "-t", name, "prefix", "C-b", ";",
            "set-window-option", "-t", name, "remain-on-exit", "on", ";",
            "pipe-pane", "-o", "-t", name, pipe.replace("#", "##"), ";",
            "wait-for", "-S", barrier)
        try:
            from .updates import update_lock
            # Lock only the tmux creation window, never a worker's lifetime.
            # `flock` is not a portable remote dependency (and is absent on
            # macOS/BSD hosts), so use an atomic POSIX directory lock instead.
            # The lock has a bounded wait; an uncertain launch remains an
            # explicit error rather than racing a release activation.
            if host is not None:
                lock_script = (
                    'lock=$1; shift; i=0; '
                    'while ! mkdir "$lock" 2>/dev/null; do '
                    'i=$((i+1)); [ "$i" -lt 100 ] || exit 75; sleep 0.05; done; '
                    '"$@"; code=$?; rmdir "$lock" 2>/dev/null || true; exit "$code"'
                )
                cmd = ["sh", "-c", lock_script, "aiko-remote-lock",
                       f"{home}/.aiko/update.lock.d", *cmd]
            with update_lock():
                self._run(target, cmd)
                # Some tmux builds exit 0 after a server startup failure.
                self._run(target, self._tmux("has-session", "-t", name))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            # The command may have reached tmux before a connection broke.
            # Keep the registry entry so a later client can reconcile it.
            row.update(error=str(exc), state="unknown", session_state="unknown")
            self._save(row)
            raise RuntimeError(f"session {sid} launch uncertain: {exc}") from exc
        row.update(state="running", session_state="live")
        return self._save(row)

    def get(self, session_id) -> dict:
        """Reconcile persisted status against the receipt and tmux, not PIDs."""
        row = self._load(session_id)
        if row["state"] in TERMINAL_STATES:
            return row
        try:
            self._check_host(row)
            target = row["target"]
            receipt = self._run(target, ["cat", row["exit_path"]], check=False)
            code = receipt.stdout.strip()
            if receipt.returncode == 0 and code.isdigit():
                row.update(exit_code=int(code), state="completed" if int(code) == 0 else "failed",
                           session_state="exited", error=None)
            else:
                result = self._run(target, self._tmux(
                    "list-panes", "-t", row["tmux_session"], "-F",
                    "#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}"), check=False)
                if result.returncode:
                    # Missing server/session is lost, never successful completion.
                    missing = any(s in result.stderr.lower() for s in
                                  ("no server running", "no such file", "can't find", "cannot find"))
                    row.update(state="lost" if missing else "unknown",
                               session_state="unknown", error=result.stderr.strip())
                else:
                    dead, status, signal = result.stdout.strip().split("|", 2)
                    if dead == "1":
                        code = int(status) if status.isdigit() else None
                        row.update(state="completed" if code == 0 and not signal else "failed",
                                   session_state="exited", exit_code=code,
                                   error=f"signal {signal}" if signal else None)
                    else:
                        row.update(state="running", session_state="live", error=None)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            row.update(state="unknown", session_state="unknown", error=str(exc))
        row["updated_at"] = time.time()
        return self._save(row)

    def list(self) -> list[dict]:
        with self._db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM direct_sessions ORDER BY rowid")]
        return [self.get(sid) for sid in ids]

    def attach_command(self, session_id, target="local") -> list[str]:
        row = self._load(session_id)
        if row["target"] != target:
            raise ValueError(f"session belongs to target {row['target']!r}")
        self._check_host(row)
        return self._argv(target, self._tmux("attach-session", "-t", row["tmux_session"]),
                          interactive=True)

    def read_transcript(self, session_id, tail=None) -> str:
        row = self._load(session_id)
        self._check_host(row)
        if tail is not None and (not isinstance(tail, int) or tail < 0):
            raise ValueError("tail must be a nonnegative number of bytes")
        argv = ["cat", row["transcript"]] if tail is None else [
            "tail", "-c", str(tail), row["transcript"]]
        result = self._run(row["target"], argv, check=False)
        if result.returncode:
            return f"(no transcript for {session_id})"
        return result.stdout

    def send_input(self, session_id, text) -> dict:
        row = self.get(session_id)
        if row["state"] != "running":
            return {"error": "session not alive"}
        self._run(row["target"], self._tmux("send-keys", "-t", row["tmux_session"],
                                           "-l", "--", text))
        self._run(row["target"], self._tmux("send-keys", "-t", row["tmux_session"], "Enter"))
        return {"sent": True, "session_id": session_id}

    def stop(self, session_id) -> dict:
        """Explicit cancellation only; detaching/closing clients never calls this."""
        row = self.get(session_id)
        if row["state"] in TERMINAL_STATES:
            return row
        self._check_host(row)
        self._run(row["target"], self._tmux("kill-session", "-t", row["tmux_session"]))
        row.update(state="cancelled", session_state="exited", exit_code=None,
                   updated_at=time.time(), error=None)
        return self._save(row)

    def claim_outcome(self, session_id) -> bool:
        """Persist feedback deduplication across backend reconstruction."""
        with self._db() as db:
            cursor = db.execute("""UPDATE direct_sessions SET outcome_recorded=1
                WHERE id=? AND outcome_recorded=0""", (session_id,))
            return cursor.rowcount == 1
