"""Immutable wheel releases and idle-only agent CLI updates.

Public entry points: update(), update_tool(), run_pending_updates(), update_lock(),
stage_release(), activate_release().
Session managers must hold update_lock() through process creation. The lock
coordinates Aiko writers; unrelated programs do not honor it, so tool updates
also inspect the complete host process table immediately before execution.
No release garbage collection is automatic: existing sessions may still use
any previous interpreter. State and recovery journals live in ~/.aiko/updates.

Configuration (owned by aiko.config):
  updates: {source, install_root, bin_dir, python, restart_command}
  servers: [{name, ssh_host, install_root, bin_dir, python, restart_command}]
  agents: [{name, command, update_command: [executable, ...], process_names: []}]
Server paths expand on the server. Commands are argv lists, never shell text.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile


class UpdateError(Exception):
    """A deliberately sanitized error safe for CLI/TUI presentation."""


_THREAD_LOCK = threading.RLock()
_LOCK_STATE = threading.local()
_DEFAULT_ROOT = "~/.local/share/aiko"
_DEFAULT_COMMANDS = {"codex": "update", "opencode": "upgrade", "hermes": "update"}


def _state_dir() -> Path:
    return Path.home() / ".aiko" / "updates"


@contextmanager
def update_lock():
    """Reentrant host-wide update/spawn lock at ~/.aiko/update.lock.

    Hold only through spawn, not through the lifetime of a session. All Aiko
    processes on a host must use this path, including the daemon. flock is
    released on crash; descriptors are not inherited by spawned children.
    """
    with _THREAD_LOCK:
        if getattr(_LOCK_STATE, "depth", 0):
            _LOCK_STATE.depth += 1
            try:
                yield
            finally:
                _LOCK_STATE.depth -= 1
            return
        parent = Path.home() / ".aiko"
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(parent / "update.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _LOCK_STATE.depth = 1
            yield
        finally:
            _LOCK_STATE.depth = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (ValueError, OSError):
        raise UpdateError("Updater state is unreadable; inspect the state file before retrying") from None


def _argv(value, label="command") -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item or "\x00" in item for item in value
    ):
        raise UpdateError(f"Configure {label} as a nonempty argv list")
    return value


def _run(argv, *, input=None, cwd=None, timeout=900) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, input=input, cwd=cwd, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise UpdateError("Command unavailable or timed out") from None
    if result.returncode:
        # Updaters/pip/SSH may echo private URLs, environment values or argv.
        raise UpdateError(f"Command failed (exit {result.returncode}); output withheld")
    return result


def _path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise UpdateError("Invalid installation path")
    path = Path.home() / value[2:] if value.startswith("~/") else Path(value)
    if not path.is_absolute():
        raise UpdateError("Installation paths must be absolute or start with ~/")
    return path


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_artifact(path: Path | str, sha256: str) -> None:
    """Verify exact wheel bytes before installing on any host."""
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise UpdateError("Invalid artifact SHA256")
    if _sha(Path(path)) != sha256:
        raise UpdateError("Artifact SHA256 mismatch; release was not staged")


def _wheel_identity(path: Path) -> dict:
    if path.suffix != ".whl" or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", path.name):
        raise UpdateError("Expected a local wheel file")
    try:
        from email.parser import BytesParser
        with zipfile.ZipFile(path) as wheel:
            names = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise UpdateError("Wheel must contain exactly one distribution")
            metadata = BytesParser().parsebytes(wheel.read(names[0]))
            if metadata.get("Name", "").lower().replace("_", "-") != "aiko":
                raise UpdateError("Wheel distribution must be aiko")
            version = metadata.get("Version")
            if not version:
                raise UpdateError("Wheel has no version metadata")
    except (zipfile.BadZipFile, KeyError):
        raise UpdateError("Invalid wheel archive") from None
    return {"filename": path.name, "sha256": _sha(path), "version": version}


def _stage(wheel: Path, artifact: dict, settings: dict) -> dict:
    verify_artifact(wheel, artifact["sha256"])
    _wheel_identity(wheel)
    root = _path(settings.get("install_root", _DEFAULT_ROOT))
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / artifact["sha256"]
    marker = release / "release.json"
    if release.exists() or release.is_symlink():
        if release.is_symlink() or _read_json(marker, {}).get("sha256") != artifact["sha256"]:
            raise UpdateError("Release path already exists without matching completion metadata")
        return {"status": "staged", "release": str(release), "reused": True}
    release.mkdir()
    try:
        python = settings.get("python", sys.executable)
        if not isinstance(python, str) or not python or python.startswith("-"):
            raise UpdateError("Configure python as an executable path")
        _run([python, "-m", "venv", str(release / "venv")])
        interpreter = str(release / "venv" / "bin" / "python")
        _run([interpreter, "-m", "pip", "install", "--disable-pip-version-check", str(wheel)])
        _run([interpreter, "-m", "pip", "check"])
        _run([interpreter, "-I", "-c", "import aiko.cli, aikod.__main__"])
        for command in ("aiko", "aikod", "aao"):
            if not (release / "venv" / "bin" / command).is_file():
                raise UpdateError("Installed wheel is missing a required entry point")
        _atomic_json(marker, artifact)
    except BaseException:
        # Only this call's new, never-activated release is removable.
        shutil.rmtree(release)
        raise
    return {"status": "staged", "release": str(release), "reused": False}


def _launchers(root: Path, bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for command in ("aiko", "aikod", "aao"):
        # Read current once. Both script and interpreter belong to that release
        # even when another updater swaps current while the process is running.
        script = ("#!/bin/sh\nset -eu\n"
                  f"release=$(/usr/bin/readlink {shlex.quote(str(root / 'current'))})\n"
                  'case "$release" in /*) ;; *) exit 126 ;; esac\n'
                  f'exec "$release/venv/bin/python" "$release/venv/bin/{command}" "$@"\n')
        fd, name = tempfile.mkstemp(prefix=f".{command}-", dir=bin_dir)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(script)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o755)
            os.replace(name, bin_dir / command)
        finally:
            Path(name).unlink(missing_ok=True)


def _switch(root: Path, release: str) -> None:
    link = root / (".current-" + uuid.uuid4().hex)
    try:
        link.symlink_to(release)
        os.replace(link, root / "current")
    finally:
        link.unlink(missing_ok=True)


def _activate(release: str, artifact: dict, settings: dict) -> dict:
    root = _path(settings.get("install_root", _DEFAULT_ROOT))
    if not re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]):
        raise UpdateError("Invalid artifact SHA256")
    expected = root / "releases" / artifact["sha256"]
    if Path(release) != expected or expected.is_symlink():
        raise UpdateError("Release path does not match artifact")
    if _read_json(expected / "release.json", {}).get("sha256") != artifact["sha256"]:
        raise UpdateError("Release is not completely staged")
    restart = settings.get("restart_command")
    if restart is not None:
        _argv(restart, "restart_command")
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise UpdateError("current must be a symlink")
    previous = os.readlink(current) if current.is_symlink() else None
    journal = {"status": "activating", "release": release, "previous": previous,
               "sha256": artifact["sha256"], "created_at": time.time()}
    journal_path = _state_dir() / "activations" / (uuid.uuid4().hex + ".json")
    _atomic_json(journal_path, journal)
    _launchers(root, _path(settings.get("bin_dir", "~/.local/bin")))
    _switch(root, release)
    journal["status"] = "activated"
    _atomic_json(journal_path, journal)
    if restart:
        try:
            _run(restart, timeout=120)
        except UpdateError as exc:
            if previous is None:
                current.unlink()
            else:
                _switch(root, previous)
            journal.update(status="restart_failed", error=str(exc), rolled_back=True)
            if previous is not None:
                try:
                    _run(restart, timeout=120)
                    journal["rollback_restart"] = "succeeded"
                except UpdateError:
                    journal["rollback_restart"] = "failed"
            _atomic_json(journal_path, journal)
            return journal
    journal["journal"] = str(journal_path)
    return journal


def _config() -> dict:
    from aiko.config import load_config
    return load_config()


def stage_release(wheel, *, target="local", settings=None, sha256=None) -> dict:
    """Stage only, for the initial installer. Does not switch current/restart.

    target is 'local' or a configured server name. settings optionally overrides
    that host's install_root/bin_dir/python/restart_command. Pass sha256 when
    consuming an artifact with an independently known digest. Returns a receipt
    for activate_release(receipt). No existing release is ever modified.
    """
    try:
        with update_lock():
            config = _config()
            server = None
            if target == "local":
                host_settings = dict(config.get("updates", {}))
            else:
                server = next((s for s in _servers(config) if s["name"] == target), None)
                if server is None:
                    raise UpdateError("Unknown SSH update target")
                host_settings = _host_settings(server)
            host_settings.update(settings or {})
            _state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
            source = Path(wheel).expanduser().absolute()
            with tempfile.TemporaryDirectory(prefix="stage-", dir=_state_dir()) as tmp:
                path = Path(tmp) / source.name
                shutil.copyfile(source, path)
                if sha256 is not None:
                    verify_artifact(path, sha256)
                artifact = _wheel_identity(path)
                if server is None:
                    result = _stage(path, artifact, host_settings)
                else:
                    result = _remote(server, {"action": "stage", "artifact": artifact,
                        "wheel": base64.b64encode(path.read_bytes()).decode("ascii"),
                        "settings": _host_settings(host_settings)})
            return {**result, "artifact": artifact, "target": target,
                    "settings": _host_settings(host_settings)}
    except Exception as exc:
        return {"status": "failed", "target": target, "error": _safe_error(exc)}


def activate_release(receipt: dict) -> dict:
    """Activate a successful stage_release receipt; retain rollback metadata."""
    try:
        with update_lock():
            if receipt.get("status") != "staged":
                raise UpdateError("Activation requires a successful stage receipt")
            target = receipt["target"]
            if target == "local":
                return _activate(receipt["release"], receipt["artifact"], receipt["settings"])
            server = next((s for s in _servers(_config()) if s["name"] == target), None)
            if server is None:
                raise UpdateError("Unknown SSH update target")
            return _remote(server, {"action": "activate", "release": receipt["release"],
                                   "artifact": receipt["artifact"], "settings": receipt["settings"]})
    except Exception as exc:
        return {"status": "failed", "error": _safe_error(exc)}


def _servers(config: dict) -> list[dict]:
    servers = []
    for server in config.get("servers", []):
        if not server.get("ssh_host"):
            continue
        name = server.get("name", server["ssh_host"])
        if not isinstance(name, str) or name == "local" or any(s["name"] == name for s in servers):
            raise UpdateError("Server update target names must be unique and cannot be local")
        servers.append({**server, "name": name})
    return servers


def _remote(server: dict, request: dict) -> dict:
    host = server["ssh_host"]
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_@.:-]+", host) or host.startswith("-"):
        raise UpdateError("Invalid ssh_host; configure an SSH alias or user@host")
    # Only the fixed bootstrap crosses the SSH shell. Config, wheel bytes and
    # worker source travel over stdin, not as shell expressions or process argv.
    bootstrap = ("import json,sys; p=json.load(sys.stdin); "
                 "n={'__name__':'aiko_update_worker'}; exec(p['code'],n); "
                 "print(json.dumps(n['_dispatch'](p['request'])))")
    payload = json.dumps({"code": Path(__file__).read_text(), "request": request})
    result = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "--", host,
                   "python3 -c " + shlex.quote(bootstrap)], input=payload, timeout=1800)
    try:
        answer = json.loads(result.stdout)
        if not isinstance(answer, dict):
            raise ValueError
    except ValueError:
        raise UpdateError("SSH updater returned an invalid response") from None
    return answer


def _dispatch(request: dict) -> dict:
    """Standalone stdlib-only remote worker; no installed Aiko is required."""
    try:
        with update_lock():
            action = request["action"]
            if action == "stage":
                artifact = request["artifact"]
                filename = artifact["filename"]
                if Path(filename).name != filename or not filename.endswith(".whl"):
                    raise UpdateError("Invalid artifact filename")
                _state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
                with tempfile.TemporaryDirectory(prefix="wheel-", dir=_state_dir()) as tmp:
                    wheel = Path(tmp) / filename
                    wheel.write_bytes(base64.b64decode(request["wheel"], validate=True))
                    return _stage(wheel, artifact, request["settings"])
            if action == "activate":
                return _activate(request["release"], request["artifact"], request["settings"])
            if action == "tool":
                return _execute_tool(request["name"], request["agent"])
            raise UpdateError("Unknown updater operation")
    except Exception as exc:
        return {"status": "failed", "error": _safe_error(exc)}


def _safe_error(exc: Exception) -> str:
    return str(exc) if isinstance(exc, UpdateError) else "Updater operation failed; diagnostic output withheld"


def update(wheel=None, local_only=False) -> dict:
    """Stage the same wheel everywhere, activate servers, then activate local.

    Failed stage means no activation anywhere. An activation failure stops the
    rollout; successful earlier servers remain on the new release and their
    journals retain rollback targets. Local stays unchanged on server failure.
    """
    report = {"status": "failed", "hosts": {}}
    try:
        with update_lock():
            config = _config()
            settings = config.get("updates", {})
            servers = [] if local_only else _servers(config)
            _state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
            report_path = _state_dir() / "runs" / (uuid.uuid4().hex + ".json")
            report["journal"] = str(report_path)
            with tempfile.TemporaryDirectory(prefix="build-", dir=_state_dir()) as tmp:
                if wheel is None:
                    source = settings.get("source")
                    if not source:
                        raise UpdateError("Configure updates.source or provide --wheel")
                    source = _path(source)
                    _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", tmp, str(source)])
                    wheels = list(Path(tmp).glob("*.whl"))
                    if len(wheels) != 1:
                        raise UpdateError("Source build must produce exactly one wheel")
                    wheel_path = wheels[0]
                else:
                    supplied = Path(wheel).expanduser().absolute()
                    _wheel_identity(supplied)
                    wheel_path = Path(tmp) / supplied.name
                    shutil.copyfile(supplied, wheel_path)
                artifact = _wheel_identity(wheel_path)
                report["artifact"] = artifact
                _atomic_json(report_path, report)
                try:
                    report["hosts"]["local"] = _stage(wheel_path, artifact, settings)
                except Exception as exc:
                    report["hosts"]["local"] = {"status": "failed", "phase": "stage", "error": _safe_error(exc)}
                    _atomic_json(report_path, report)
                    return report
                encoded = base64.b64encode(wheel_path.read_bytes()).decode("ascii")
                for server in servers:
                    try:
                        result = _remote(server, {"action": "stage", "artifact": artifact,
                                                  "wheel": encoded, "settings": _host_settings(server)})
                    except Exception as exc:
                        result = {"status": "failed", "error": _safe_error(exc)}
                    report["hosts"][server["name"]] = {**result, "phase": "stage"}
                    _atomic_json(report_path, report)
                if any(host["status"] != "staged" for host in report["hosts"].values()):
                    return report
                for server in servers:
                    name = server["name"]
                    try:
                        result = _remote(server, {"action": "activate", "artifact": artifact,
                            "release": report["hosts"][name]["release"], "settings": _host_settings(server)})
                    except Exception as exc:
                        result = {"status": "failed", "error": _safe_error(exc), "activation_uncertain": True}
                    report["hosts"][name] = {**result, "phase": "activate"}
                    _atomic_json(report_path, report)
                    if result["status"] != "activated":
                        report["status"] = "partial"
                        _atomic_json(report_path, report)
                        return report
                try:
                    result = _activate(report["hosts"]["local"]["release"], artifact, settings)
                except Exception as exc:
                    result = {"status": "failed", "error": _safe_error(exc)}
                report["hosts"]["local"] = {**result, "phase": "activate"}
                report["status"] = "updated" if result["status"] == "activated" else "partial"
                _atomic_json(report_path, report)
                return report
    except Exception as exc:
        report["error"] = _safe_error(exc)
        if report.get("journal"):
            try:
                _atomic_json(Path(report["journal"]), report)
            except OSError:
                pass
        return report


def _host_settings(server: dict) -> dict:
    # Do not send server auth tokens or unrelated config to the SSH worker.
    return {key: server[key] for key in ("install_root", "bin_dir", "python", "restart_command") if key in server}


def _processes() -> list[tuple[int, str, str]]:
    try:
        output = _run(["ps", "-axo", "pid=,comm=,args=", "-ww"], timeout=15).stdout
        rows = []
        for line in output.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3 or not fields[0].isdigit():
                raise ValueError
            rows.append((int(fields[0]), fields[1], fields[2]))
        if not rows:
            raise ValueError
        return rows
    except (UpdateError, ValueError):
        raise UpdateError("Process scan unavailable; tool update deferred") from None


def _matches_process(comm: str, args: str, names: set[str]) -> bool:
    def matches(token):
        base = token.rsplit("/", 1)[-1].lower().removesuffix(".exe")
        return base in names or any(base == name + suffix for name in names for suffix in (".js", ".py"))
    if matches(comm):
        return True
    # Match executable identities, not arbitrary arguments such as
    # `aiko update-tool codex`, Python -c payloads or a user's prompt text.
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    if not tokens:
        return False
    if matches(tokens[0]):
        return True
    executable = Path(tokens[0]).name.lower()
    if re.fullmatch(r"(?:python[0-9.]*|node|nodejs|bun|deno)", executable):
        for token in tokens[1:]:
            if token in ("-c", "-e", "--eval", "-p", "--print"):
                return False
            if token.startswith("-"):
                continue
            return matches(token)
    # Never mistake Aiko's own `update-tool codex` invocation for a live
    # Codex process.  The process scanner already excludes this process by
    # PID, but a second Aiko client can be running at the same time.
    if executable in {"aiko", "aikod", "aao"}:
        return False
    # `ps` does not quote an executable path containing spaces. In that
    # representation shlex sees only its first component (for example,
    # `/Applications/Codex Suite/codex app-server` becomes two tokens). A
    # path-segment match is still an executable identity, not a prompt match;
    # it deliberately errs on the safe side by deferring an update.
    for name in names:
        escaped = re.escape(name)
        if re.search(r"(?:^|[\s/])" + escaped + r"(?:\.(?:js|py|exe))?(?=\s|$)", args, re.I):
            return True
    return False


def _tmux_busy(names: set[str]) -> bool:
    """Include workers launched under the shared lock but not yet exec'd.

    SessionManager sets @aiko_agent before releasing the spawn lock. Completed
    remain-on-exit panes do not block updates. Other/external sessions are still
    covered by ps. Missing tmux/socket means there are no managed tmux workers;
    other scan errors fail closed.
    """
    tmux = shutil.which("tmux")
    if tmux is None:
        return False
    try:
        result = subprocess.run([tmux, "-L", "aiko-workers", "-f", "/dev/null",
                                 "list-panes", "-a", "-F", "#{@aiko_agent}\t#{pane_dead}\t#{pane_current_command}"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise UpdateError("Worker scan unavailable; tool update deferred") from None
    if result.returncode:
        if any(message in result.stderr.lower() for message in ("no server running", "no such file or directory")):
            return False
        raise UpdateError("Worker scan unavailable; tool update deferred")
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or fields[1] not in ("0", "1"):
            raise UpdateError("Worker scan unavailable; tool update deferred")
        agent, dead, command = fields
        if dead == "0" and (agent.lower() in names or command.lower() in names):
            return True
    return False


def _busy(names: set[str]) -> dict | None:
    try:
        active = [pid for pid, comm, args in _processes()
                  if pid != os.getpid() and _matches_process(comm, args, names)]
        if active:
            return {"status": "deferred", "reason": "busy", "active_pids": active}
        if _tmux_busy(names):
            return {"status": "deferred", "reason": "busy", "managed_worker": True}
    except UpdateError:
        return {"status": "deferred", "reason": "scan_unavailable"}
    return None


def _execute_tool(name: str, agent: dict) -> dict:
    binary = agent.get("command", name)
    if isinstance(binary, list):
        binary = _argv(binary)[0]
    if not isinstance(binary, str) or not binary or "\x00" in binary:
        raise UpdateError("Configure agent command as an executable")
    aliases = agent.get("process_names", [])
    if not isinstance(aliases, list) or any(not isinstance(item, str) or not item for item in aliases):
        raise UpdateError("process_names must be a list of executable names")
    names = {name.lower(), Path(binary).name.lower(), *(Path(item).name.lower() for item in aliases)}
    command = agent.get("update_command")
    if command is not None:
        command = _argv(command, "update_command")
    elif name not in _DEFAULT_COMMANDS:
        return {"status": "configure", "error": "Configure this agent's update_command argv"}
    busy = _busy(names)
    if busy:
        return busy
    if command is None:
        subcommand = _DEFAULT_COMMANDS[name]
        # A default is usable only when this installation advertises it.
        help_text = _run([binary, "--help"], timeout=30).stdout
        if not re.search(r"(?m)^\s*" + re.escape(subcommand) + r"(?:\s|$)", help_text):
            return {"status": "configure", "error": "Installed tool does not advertise an updater; configure update_command"}
        command = [binary, subcommand]
    # Help verification is a subprocess, so scan again immediately before the
    # mutating command. No assumption that resume support makes hot updates safe.
    busy = _busy(names)
    if busy:
        return busy
    _run(command, timeout=1800)
    return {"status": "updated"}


def _pending() -> list:
    queue = _read_json(_state_dir() / "pending.json", [])
    if not isinstance(queue, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                                         or not isinstance(item.get("target"), str) for item in queue):
        raise UpdateError("Pending queue is invalid; inspect it before retrying")
    return queue


def _tool(name: str, target: str, config: dict) -> dict:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise UpdateError("Invalid tool name")
    agent = next((item for item in config.get("agents", []) if item.get("name") == name), {})
    if target == "local":
        return _execute_tool(name, agent)
    server = next((item for item in _servers(config) if item["name"] == target), None)
    if server is None:
        raise UpdateError("Unknown SSH update target")
    allowed = {key: agent[key] for key in ("command", "update_command", "process_names") if key in agent}
    try:
        return _remote(server, {"action": "tool", "name": name, "agent": allowed})
    except UpdateError:
        return {"status": "deferred", "reason": "remote_unavailable"}


def update_tool(name, target="local") -> dict:
    """Update an idle host's CLI; persist a deduplicated queue entry if busy.

    Only names and target identities are queued. Trusted commands are reloaded
    at execution, never retained with possible credentials in the queue.
    """
    try:
        with update_lock():
            queue = _pending()
            result = {**_tool(name, target, _config()), "name": name, "target": target}
            remaining = [item for item in queue if (item["name"], item["target"]) != (name, target)]
            if result["status"] in ("deferred", "failed"):
                existing = next((item for item in queue if (item["name"], item["target"]) == (name, target)), {})
                if result["status"] == "deferred" or existing:
                    remaining.append({"name": name, "target": target,
                                      "queued_at": existing.get("queued_at", time.time()),
                                      "reason": result.get("reason", "failed")})
            _atomic_json(_state_dir() / "pending.json", remaining)
            return result
    except Exception as exc:
        return {"status": "failed", "name": name, "target": target, "error": _safe_error(exc)}


def run_pending() -> dict:
    """Retry deferred tools; suitable for a TUI background worker/poll hook.

    Call from a worker thread, not the UI event loop: installation can block.
    Failures remain queued for the next explicit retry; no background daemon
    or unbounded retry loop is started by this module.
    """
    try:
        with update_lock():
            entries = _pending()
            results = [update_tool(item["name"], item["target"]) for item in entries]
            return {"status": "checked", "results": results, "pending": len(_pending())}
    except Exception as exc:
        return {"status": "failed", "error": _safe_error(exc)}


def run_pending_updates() -> list[dict]:
    """CLI/TUI polling contract: one result per pending tool update."""
    result = run_pending()
    return result["results"] if result["status"] == "checked" else [result]


process_pending_updates = run_pending_updates
