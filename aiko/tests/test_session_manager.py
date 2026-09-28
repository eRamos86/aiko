"""Durability tests use only temporary homes and a private tmux socket directory."""
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import pytest

from aiko.session_manager import SessionManager, SOCKET_NAME


@pytest.fixture
def tmux_env(monkeypatch):
    if not shutil.which("tmux"):
        pytest.skip("tmux is required for worker integration tests")
    # UNIX socket paths must remain short on macOS.
    with tempfile.TemporaryDirectory(prefix="aiko-tmux-", dir="/tmp") as directory:
        monkeypatch.setenv("TMUX_TMPDIR", directory)
        monkeypatch.delenv("TMUX", raising=False)
        yield
        subprocess.run(["tmux", "-L", SOCKET_NAME, "kill-server"],
                       capture_output=True, timeout=10)


def config(script='print("ready", flush=True); input()'):
    return {"agents": [{"name": "worker", "command": sys.executable,
                        "interactive": [sys.executable, "-u", "-c", script],
                        "one_shot": ["-u", "-c", script, "{prompt}"]}],
            "servers": [{"name": "remote", "ssh_host": "poop"}]}


def eventually(manager, sid, state, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = manager.get(sid)
        if row["state"] == state:
            return row
        time.sleep(0.03)
    pytest.fail(f"expected {state}, got {row}")


def test_default_config_and_private_db(monkeypatch):
    path = Path.home() / ".aiko"
    path.mkdir()
    (path / "config.yaml").write_text("agents:\n  - name: shell\n    command: sh\n")
    manager = SessionManager()
    assert manager.agents["shell"]["command"] == "sh"
    assert stat.S_IMODE(manager.db_path.stat().st_mode) == 0o600
    manager.db_path.chmod(0o644)
    assert stat.S_IMODE(SessionManager().db_path.stat().st_mode) == 0o600
    assert manager.list() == []


@pytest.mark.parametrize("target", ["../poop", "-poop", ".", "..", "a/b", "a:b",
                                        "a b", "a;touch x", "a\n", "é", "", "x" * 65])
def test_strict_target_names(target, tmp_path):
    cfg = config()
    cfg["servers"].append({"name": target, "ssh_host": "poop"})
    with pytest.raises(ValueError, match="target"):
        SessionManager(cfg).open("worker", tmp_path, target)


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "poop;touch", "poop$(id)",
                                      "poop\nother", "poop other", "", None])
def test_reject_unsafe_ssh_hosts(host, tmp_path):
    cfg = config()
    cfg["servers"][0]["ssh_host"] = host
    with pytest.raises(ValueError, match="ssh_host"):
        SessionManager(cfg).open("worker", tmp_path, "remote")


def test_unconfigured_target_never_runs_ssh(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must not run a command for an unknown target")
    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="ssh_host"):
        SessionManager(config()).open("worker", tmp_path, "other-host")


def test_restart_reconstruct_preserves_worker_and_attach(tmux_env, tmp_path):
    cfg = config()
    # The creating interpreter exits completely; no parent process keeps the
    # worker alive. A different manager recovers it solely through SQLite/tmux.
    script = ('import json,sys; from pathlib import Path; '
              'from aiko.session_manager import SessionManager; '
              'Path.home=classmethod(lambda cls: Path(sys.argv[1])); '
              'print(json.dumps(SessionManager(json.loads(sys.argv[2]))'
              '.open("worker", sys.argv[3])))')
    result = subprocess.run([sys.executable, "-c", script, str(Path.home()),
                             json.dumps(cfg), str(tmp_path)], capture_output=True,
                            text=True, check=True, timeout=15)
    original = json.loads(result.stdout)
    manager = SessionManager(cfg)
    row = manager.list()[0]
    assert row["id"] == original["id"]
    assert row["state"] == "running"
    assert manager.attach_command(row["id"])[-3:] == ["attach-session", "-t", row["tmux_session"]]
    assert manager.send_input(row["id"], "done")["sent"]
    finished = eventually(manager, row["id"], "completed")
    assert finished["exit_code"] == 0
    assert "ready" in manager.read_transcript(row["id"])
    assert SessionManager(cfg).get(row["id"])["state"] == "completed"
    assert not manager.send_input(row["id"], "again").get("sent")


@pytest.mark.parametrize("exit_code,state", [(0, "completed"), (7, "failed"), (130, "failed")])
def test_fast_exit_receipt_and_log_survive_tmux_removal(tmux_env, tmp_path, exit_code, state):
    cfg = config(f'import sys; print("immediate output", flush=True); sys.exit({exit_code})')
    manager = SessionManager(cfg)
    row = manager.open("worker", tmp_path)
    eventually(manager, row["id"], state)
    manager._run("local", manager._tmux("kill-session", "-t", row["tmux_session"]))
    # Force receipt reconciliation instead of relying on the cached final state.
    row.update(state="running")
    manager._save(row)
    restored = SessionManager(cfg)
    assert restored.get(row["id"])["exit_code"] == exit_code
    assert restored.get(row["id"])["state"] == state
    assert "immediate output" in restored.read_transcript(row["id"])
    assert restored.read_transcript(row["id"], 0) == ""


def test_paths_prompt_and_tmux_format_characters_are_literal(tmux_env, tmp_path, monkeypatch):
    home = tmp_path / "home ' spaces $dollar #{pid}"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    cwd = tmp_path / "repo ' quoted ; $(touch INJECTED) #{pid}"
    cwd.mkdir()
    prompt = "literal ' ; $(touch INJECTED) `touch INJECTED`\nnext line #{pid}"
    cfg = config('import os,sys,json; print(json.dumps([os.getcwd(),sys.argv[1]]), flush=True)')
    manager = SessionManager(cfg)
    before = Path.cwd()
    row = manager.open("worker", cwd, prompt=prompt)
    eventually(manager, row["id"], "completed")
    output = manager.read_transcript(row["id"])
    assert json.loads(output.strip()) == [str(cwd.resolve()), prompt]
    assert Path.cwd() == before
    assert not (cwd / "INJECTED").exists()
    assert stat.S_IMODE(Path(row["transcript"]).stat().st_mode) == 0o600


def test_interactive_default_is_command_and_placeholder_is_single_argument(tmp_path, monkeypatch):
    cfg = {"agents": [{"name": "shell", "command": "sh"}]}
    manager = SessionManager(cfg)
    monkeypatch.setattr(manager, "_start", lambda *args: args)
    assert manager.open("shell", tmp_path)[3] == ["sh"]
    cfg["agents"][0]["interactive"] = ["sh", "--prompt={prompt}"]
    assert manager.open("shell", tmp_path, prompt="a ' ; b")[3] == ["sh", "--prompt=a ' ; b"]


def test_dispatch_refuses_main_unless_explicit(tmux_env, tmp_path):
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    manager = SessionManager(config('print("ok")'))
    for choice in [False, "yes", 1]:
        with pytest.raises(ValueError, match="protected branch"):
            manager.dispatch("worker", tmp_path, "task", repo_direct=choice)
    assert manager.list() == []
    row = manager.dispatch("worker", tmp_path, "task", repo_direct=True)
    eventually(manager, row["id"], "completed")


def test_lost_and_cancelled_are_not_success(tmux_env, tmp_path):
    manager = SessionManager(config())
    lost = manager.open("worker", tmp_path)
    manager._run("local", manager._tmux("kill-session", "-t", lost["tmux_session"]))
    assert manager.get(lost["id"])["state"] == "lost"
    stopped = manager.open("worker", tmp_path)
    assert manager.stop(stopped["id"])["state"] == "cancelled"
    assert SessionManager(config()).get(stopped["id"])["state"] == "cancelled"
    assert manager.get(stopped["id"])["exit_code"] is None


@pytest.fixture
def simulated_ssh(tmp_path, monkeypatch, tmux_env):
    """Run SSH's quoted remote command in a real shell, without network access."""
    home = tmp_path / "remote home ' #{pid}"
    home.mkdir()
    real_run = subprocess.run
    calls = []

    def run(argv, **kwargs):
        if argv[0] == "ssh":
            calls.append(argv)
            assert argv[argv.index("--") + 1] == "poop"
            env = {**os.environ, "HOME": str(home)}
            return real_run(["sh", "-c", argv[-1]], env=env, **kwargs)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return home, calls


def test_remote_transport_reconstruction_quoting_and_attach(simulated_ssh):
    home, calls = simulated_ssh
    cwd = home / "repo ' ; $(touch INJECTED) #{pid}"
    cwd.mkdir()
    cfg = config('import os; print(os.getcwd(), flush=True); input()')
    manager = SessionManager(cfg)
    row = manager.open("worker", "~/" + cwd.name, "remote")
    assert row["id"].startswith("remote-")
    assert row["cwd"] == str(cwd.resolve())
    restored = SessionManager(cfg)
    assert restored.list()[0]["state"] == "running"
    attach = restored.attach_command(row["id"], "remote")
    assert attach[:2] == ["ssh", "-t"]
    assert shlex.split(attach[-1])[-3:] == ["attach-session", "-t", row["tmux_session"]]
    with pytest.raises(ValueError, match="belongs to target"):
        restored.attach_command(row["id"])
    restored.send_input(row["id"], "literal ; $(touch INJECTED)")
    eventually(restored, row["id"], "completed")
    assert str(cwd.resolve()) in restored.read_transcript(row["id"])
    assert not (cwd / "INJECTED").exists()
    assert all("python3" not in shlex.split(call[-1])[:1] for call in calls)


def test_remote_missing_cwd_and_main_protection(simulated_ssh):
    home, calls = simulated_ssh
    manager = SessionManager(config())
    with pytest.raises(ValueError, match="remote directory"):
        manager.open("worker", str(home / "missing ' ; bad"), "remote")
    assert not any("new-session" in shlex.split(call[-1]) for call in calls)
    subprocess.run(["git", "init", "-b", "main", str(home)], check=True, capture_output=True)
    with pytest.raises(ValueError, match="protected branch"):
        manager.dispatch("worker", str(home), "do it", "remote")


def test_ssh_outage_and_host_change_are_not_completion(simulated_ssh, monkeypatch):
    home, calls = simulated_ssh
    cfg = config()
    manager = SessionManager(cfg)
    row = manager.open("worker", str(home), "remote")
    with monkeypatch.context() as outage:
        outage.setattr(subprocess, "run", lambda argv, **kw:
                       subprocess.CompletedProcess(argv, 255, "", "offline"))
        assert manager.get(row["id"])["state"] == "unknown"
        assert manager.get(row["id"])["exit_code"] is None
    assert manager.get(row["id"])["state"] == "running"
    changed = config()
    changed["servers"][0]["ssh_host"] = "other-host"
    count = len(calls)
    assert SessionManager(changed).get(row["id"])["state"] == "unknown"
    assert len(calls) == count
    with pytest.raises(ValueError, match="differs"):
        SessionManager(changed).attach_command(row["id"], "remote")


def test_adapter_reconstruction_and_independent_sessions(tmux_env, tmp_path, monkeypatch):
    from aiko.adapters.local import LocalCodexAdapter
    cfg = config()
    cfg["agents"][0]["name"] = "codex"
    monkeypatch.setattr("aiko.adapters.local.SessionManager", lambda: SessionManager(cfg))
    bundle = tmp_path / "bundle.md"
    bundle.write_text("context")
    first = LocalCodexAdapter().spawn({"id": "task-1", "spec": "one", "cwd": str(tmp_path)}, str(bundle))
    second = LocalCodexAdapter().spawn({"id": "task-2", "spec": "two", "cwd": str(tmp_path)}, str(bundle))
    assert first != second
    restored = LocalCodexAdapter()
    assert restored.status(first)["alive"]
    restored.kill(first)
    assert restored.status(first)["state"] == "cancelled"
    assert restored.status(second)["alive"]
    restored.send_input(second, "finish")
    eventually(SessionManager(cfg), second, "completed")
