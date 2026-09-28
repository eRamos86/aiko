"""Updater safety tests: no production state, SSH, or package downloads."""
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time
import zipfile

import pytest

from aiko import updates as u


@pytest.fixture
def config(tmp_path, monkeypatch):
    cfg = {"updates": {"install_root": str(tmp_path / "installation with spaces"),
                       "bin_dir": str(tmp_path / "bin with spaces")},
           "agents": [], "servers": []}
    monkeypatch.setattr(u, "_config", lambda: cfg)
    monkeypatch.setattr(u, "_tmux_busy", lambda names: False)
    return cfg


@pytest.fixture
def wheel(tmp_path):
    """A real minimal wheel for offline pip/venv and launcher verification."""
    path = tmp_path / "aiko-9.9.9-py3-none-any.whl"
    main = "import sys\ndef main():\n print(sys.prefix)\n print(__file__)\n print(repr(sys.argv[1:]))\n"
    files = {
        "aiko/__init__.py": "", "aiko/cli.py": main,
        "aikod/__init__.py": "", "aikod/__main__.py": main,
        "aiko-9.9.9.dist-info/METADATA": "Metadata-Version: 2.1\nName: aiko\nVersion: 9.9.9\n",
        "aiko-9.9.9.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        "aiko-9.9.9.dist-info/entry_points.txt":
            "[console_scripts]\naiko = aiko.cli:main\naao = aiko.cli:main\naikod = aikod.__main__:main\n",
    }
    record = io.StringIO()
    writer = csv.writer(record)
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data.encode()).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(data.encode())])
    writer.writerow(["aiko-9.9.9.dist-info/RECORD", "", ""])
    files["aiko-9.9.9.dist-info/RECORD"] = record.getvalue()
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def completed(argv=(), stdout=""):
    return subprocess.CompletedProcess(argv, 0, stdout, "")


def test_hash_failure_prevents_staging(wheel, config, monkeypatch):
    calls = []
    monkeypatch.setattr(u, "_run", lambda *a, **kw: calls.append(a))
    result = u.stage_release(wheel, sha256="0" * 64)
    assert result["status"] == "failed"
    assert "SHA256 mismatch" in result["error"]
    assert not calls
    assert not Path(config["updates"]["install_root"]).exists()


def test_remote_hash_failure_prevents_install(wheel, config, monkeypatch):
    calls = []
    monkeypatch.setattr(u, "_run", lambda *a, **kw: calls.append(a))
    artifact = {**u._wheel_identity(wheel), "sha256": "0" * 64}
    result = u._dispatch({"action": "stage", "artifact": artifact,
                          "settings": config["updates"],
                          "wheel": base64.b64encode(wheel.read_bytes()).decode()})
    assert result["status"] == "failed"
    assert not calls


def test_real_wheel_staging_launchers_and_release_retention(wheel, config):
    receipt = u.stage_release(wheel, sha256=u._sha(wheel))
    assert receipt["status"] == "staged", receipt
    root = Path(config["updates"]["install_root"])
    assert not (root / "current").exists()
    result = u.activate_release(receipt)
    assert result["status"] == "activated", result
    assert os.readlink(root / "current") == receipt["release"]
    for name in ("aiko", "aikod", "aao"):
        output = subprocess.check_output([str(Path(config["updates"]["bin_dir"]) / name), "two words", "$(false)"], text=True)
        assert output.splitlines()[0] == receipt["release"] + "/venv"
        assert "site-packages" in output
        assert "['two words', '$(false)']" in output
    old = Path(receipt["release"])
    # Changed bytes produce a different immutable release at the same version.
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("aiko/extra.py", "VALUE = 2\n")
    second = u.stage_release(wheel)
    assert second["status"] == "staged", second
    assert second["release"] != receipt["release"]
    assert u.activate_release(second)["previous"] == str(old)
    assert old.is_dir()
    # A session already pinned to the old interpreter can keep using it.
    output = subprocess.check_output([str(old / "venv/bin/python"), str(old / "venv/bin/aiko")], text=True)
    assert output.splitlines()[0] == str(old / "venv")
    assert not list((old / "venv").rglob("*.egg-link"))


def test_stage_failure_does_not_activate_or_remove_old_release(wheel, config, monkeypatch):
    root = Path(config["updates"]["install_root"])
    old = root / "releases" / "old"
    old.mkdir(parents=True)
    (root / "current").symlink_to(old)
    def fail(*args, **kwargs):
        raise u.UpdateError("install failed")
    monkeypatch.setattr(u, "_run", fail)
    result = u.update(wheel=wheel)
    assert result["hosts"]["local"]["phase"] == "stage"
    assert os.readlink(root / "current") == str(old)
    assert old.exists()
    assert not (root / "releases" / u._sha(wheel)).exists()


@pytest.mark.parametrize("failed_host", ["local", "one", "two"])
def test_all_stages_must_pass_before_any_activation(wheel, config, monkeypatch, failed_host):
    config["servers"] = [{"name": name, "ssh_host": name} for name in ("one", "two")]
    events = []
    def stage(*args):
        events.append(("stage", "local"))
        if failed_host == "local":
            raise u.UpdateError("stage failed")
        return {"status": "staged", "release": "local-release"}
    def remote(server, request):
        events.append((request["action"], server["name"]))
        return {"status": "failed" if server["name"] == failed_host else "staged", "release": "remote-release"}
    monkeypatch.setattr(u, "_stage", stage)
    monkeypatch.setattr(u, "_remote", remote)
    monkeypatch.setattr(u, "_activate", lambda *args: pytest.fail("must not activate"))
    result = u.update(wheel=wheel)
    assert result["status"] == "failed"
    assert all(action == "stage" for action, _ in events)


def test_remote_then_local_activation_and_identical_artifact(wheel, config, monkeypatch):
    config["servers"] = [{"name": name, "ssh_host": name} for name in ("one", "two")]
    events = []
    def stage(*args):
        events.append("stage-local")
        return {"status": "staged", "release": "local"}
    def remote(server, request):
        events.append(request["action"] + "-" + server["name"])
        if request["action"] == "stage":
            data = base64.b64decode(request["wheel"])
            assert data == wheel.read_bytes()
            assert hashlib.sha256(data).hexdigest() == request["artifact"]["sha256"]
            return {"status": "staged", "release": "remote"}
        return {"status": "activated", "previous": "old"}
    def activate(*args):
        events.append("activate-local")
        return {"status": "activated", "previous": "old"}
    monkeypatch.setattr(u, "_stage", stage)
    monkeypatch.setattr(u, "_remote", remote)
    monkeypatch.setattr(u, "_activate", activate)
    result = u.update(wheel=wheel)
    assert result["status"] == "updated"
    assert events == ["stage-local", "stage-one", "stage-two", "activate-one", "activate-two", "activate-local"]
    assert json.loads(Path(result["journal"]).read_text())["hosts"]["one"]["previous"] == "old"


def test_remote_activation_failure_keeps_local_staged(wheel, config, monkeypatch):
    config["servers"] = [{"name": "one", "ssh_host": "one"}]
    monkeypatch.setattr(u, "_stage", lambda *a: {"status": "staged", "release": "local"})
    monkeypatch.setattr(u, "_remote", lambda s, r: {"status": "staged", "release": "remote"}
                        if r["action"] == "stage" else {"status": "restart_failed", "previous": "old"})
    monkeypatch.setattr(u, "_activate", lambda *a: pytest.fail("local must not activate"))
    result = u.update(wheel=wheel)
    assert result["status"] == "partial"
    assert result["hosts"]["local"]["status"] == "staged"


def staged_marker(config):
    root = Path(config["updates"]["install_root"])
    artifact = {"sha256": "a" * 64}
    release = root / "releases" / artifact["sha256"]
    release.mkdir(parents=True)
    u._atomic_json(release / "release.json", artifact)
    return root, release, artifact


def test_restart_failure_rolls_back_and_journals(config, monkeypatch):
    root, release, artifact = staged_marker(config)
    old = root / "releases/old"
    old.mkdir()
    (root / "current").symlink_to(old)
    config["updates"]["restart_command"] = ["sudo", "-n", "systemctl", "restart", "aikod"]
    calls = []
    def restart(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise u.UpdateError("restart failed")
        return completed()
    monkeypatch.setattr(u, "_run", restart)
    result = u._activate(str(release), artifact, config["updates"])
    assert result["status"] == "restart_failed"
    assert result["rolled_back"] is True
    assert result["rollback_restart"] == "succeeded"
    assert os.readlink(root / "current") == str(old)
    assert release.exists()
    journal = next((u._state_dir() / "activations").glob("*.json"))
    assert json.loads(journal.read_text())["previous"] == str(old)
    assert len(calls) == 2


@pytest.mark.parametrize("comm,args", [
    ("codex", "codex app-server"),
    ("node", "node /opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js app-server"),
    ("/Applications/Codex", "/Applications/Codex.app/Contents/Resources/codex app-server"),
    ("codex", "codex exec resume 123"),
    ("/path/with", "/path/with spaces/codex app-server"),
])
def test_any_matching_process_defers_and_deduplicates(config, monkeypatch, comm, args):
    config["agents"] = [{"name": "codex", "update_command": ["updater", "secret-value"]}]
    monkeypatch.setattr(u, "_processes", lambda: [(901, comm, args)])
    monkeypatch.setattr(u, "_run", lambda *a, **kw: pytest.fail("busy tool must not update"))
    assert u.update_tool("codex")["status"] == "deferred"
    assert u.update_tool("codex")["status"] == "deferred"
    queue = u._pending()
    assert len(queue) == 1
    assert queue[0]["name"] == "codex"
    assert "secret-value" not in (u._state_dir() / "pending.json").read_text()


def test_idle_executes_trusted_argv_without_shell(config, monkeypatch):
    argv = ["/my tools/update", "$(touch BAD)"]
    config["agents"] = [{"name": "custom", "command": "/my tools/custom", "update_command": argv}]
    monkeypatch.setattr(u, "_processes", lambda: [(1, "init", "init")])
    calls = []
    monkeypatch.setattr(u, "_run", lambda command, **kw: calls.append(command) or completed())
    assert u.update_tool("custom")["status"] == "updated"
    assert calls == [argv]


@pytest.mark.parametrize("name,verb", [("codex", "update"), ("opencode", "upgrade"), ("hermes", "update")])
def test_default_updater_requires_installed_help(config, monkeypatch, name, verb):
    monkeypatch.setattr(u, "_processes", lambda: [(1, "init", "init")])
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return completed(stdout=f"Commands:\n  {verb}  Upgrade this tool\n")
    monkeypatch.setattr(u, "_run", run)
    assert u.update_tool(name)["status"] == "updated"
    assert calls == [[name, "--help"], [name, verb]]


def test_unknown_tool_never_guesses_command(config, monkeypatch):
    monkeypatch.setattr(u, "_run", lambda *a, **kw: pytest.fail("unknown updater"))
    assert u.update_tool("unknown")["status"] == "configure"


def test_missing_help_subcommand_never_upgrades(config, monkeypatch):
    monkeypatch.setattr(u, "_processes", lambda: [(1, "init", "init")])
    calls = []
    monkeypatch.setattr(u, "_run", lambda argv, **kw: calls.append(argv) or completed(stdout="Commands:\n  exec  Run\n"))
    assert u.update_tool("codex")["status"] == "configure"
    assert calls == [["codex", "--help"]]


def test_process_scan_unavailable_fails_closed_and_retries(config, monkeypatch):
    config["agents"] = [{"name": "codex", "update_command": ["codex", "update"]}]
    def unavailable():
        raise u.UpdateError("scan unavailable")
    monkeypatch.setattr(u, "_processes", unavailable)
    assert u.update_tool("codex")["reason"] == "scan_unavailable"
    monkeypatch.setattr(u, "_processes", lambda: [(1, "init", "init")])
    calls = []
    monkeypatch.setattr(u, "_run", lambda argv, **kw: calls.append(argv) or completed())
    result = u.run_pending_updates()
    assert isinstance(result, list)
    assert result[0]["status"] == "updated"
    assert not u._pending()
    assert calls == [["codex", "update"]]


def test_process_appearing_during_help_is_deferred(config, monkeypatch):
    scans = iter([[(1, "init", "init")], [(2, "codex", "codex app-server")]])
    monkeypatch.setattr(u, "_processes", lambda: next(scans))
    calls = []
    monkeypatch.setattr(u, "_run", lambda argv, **kw: calls.append(argv) or completed(stdout="  update Upgrade\n"))
    assert u.update_tool("codex")["status"] == "deferred"
    assert calls == [["codex", "--help"]]


@pytest.mark.parametrize("output", ["", "unparseable", "123 noargs"])
def test_invalid_ps_output_fails_closed(monkeypatch, output):
    monkeypatch.setattr(u, "_run", lambda *a, **kw: completed(stdout=output))
    with pytest.raises(u.UpdateError, match="Process scan unavailable"):
        u._processes()


def test_ssh_transport_quotes_only_fixed_bootstrap(config, monkeypatch):
    config["servers"] = [{"name": "server", "ssh_host": "ace@host", "auth_token": "not-forwarded"}]
    config["agents"] = [{"name": "codex", "update_command": ["/root with spaces/tool", "update"]}]
    calls = []
    def run(argv, **kw):
        calls.append((argv, json.loads(kw["input"])))
        return completed(stdout=json.dumps({"status": "deferred", "reason": "busy"}))
    monkeypatch.setattr(u, "_run", run)
    assert u.update_tool("codex", "server")["status"] == "deferred"
    argv, payload = calls[0]
    assert argv[-2] == "ace@host"
    assert shlex.split(argv[-1])[:2] == ["python3", "-c"]
    assert "/root with spaces/tool" not in " ".join(argv)
    assert "not-forwarded" not in json.dumps(payload)
    assert u._pending()[0]["target"] == "server"


def test_ssh_failure_queues_without_exposing_output(config, monkeypatch):
    config["servers"] = [{"name": "server", "ssh_host": "server"}]
    def fail(*a, **kw):
        raise u.UpdateError("Command failed; output withheld")
    monkeypatch.setattr(u, "_remote", fail)
    result = u.update_tool("codex", "server")
    assert result["status"] == "deferred"
    assert result["reason"] == "remote_unavailable"
    assert len(u._pending()) == 1


def test_failure_keeps_existing_pending_item(config, monkeypatch):
    u._atomic_json(u._state_dir() / "pending.json", [{"name": "codex", "target": "server"}])
    monkeypatch.setattr(u, "_tool", lambda *a: {"status": "failed", "error": "failed"})
    assert u.run_pending_updates()[0]["status"] == "failed"
    assert len(u._pending()) == 1


def test_lock_serializes_threads_and_is_reentrant():
    entered = threading.Event()
    def worker():
        with u.update_lock():
            entered.set()
    with u.update_lock():
        with u.update_lock():
            thread = threading.Thread(target=worker)
            thread.start()
            assert not entered.wait(0.05)
    thread.join(timeout=2)
    assert entered.is_set()


def test_subprocess_error_does_not_include_secrets(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 2, "SECRET", "SECRET"))
    with pytest.raises(u.UpdateError) as exc:
        u._run(["secret-command", "SECRET"])
    assert "SECRET" not in str(exc.value)


def test_own_updater_and_transport_arguments_do_not_count_as_tool(config, monkeypatch):
    config["agents"] = [{"name": "codex", "update_command": ["codex", "update"]}]
    monkeypatch.setattr(u, "_processes", lambda: [
        (os.getpid(), "codex", "codex update"),
        (999, "python", "python -m aiko update-tool codex"),
        (998, "python3", "python3 -c 'print(\"codex\")'"),
        (997, "aiko", "/local/bin/aiko update-tool codex"),
    ])
    calls = []
    monkeypatch.setattr(u, "_run", lambda argv, **kw: calls.append(argv) or completed())
    assert u.update_tool("codex")["status"] == "updated"
    assert calls == [["codex", "update"]]


def test_external_ancestor_app_server_still_blocks(config, monkeypatch):
    config["agents"] = [{"name": "codex", "update_command": ["codex", "update"]}]
    monkeypatch.setattr(u, "_processes", lambda: [(os.getppid(), "codex", "codex app-server")])
    assert u.update_tool("codex")["status"] == "deferred"


def test_starting_tmux_worker_blocks_before_exec(config, monkeypatch):
    monkeypatch.setattr(u, "_processes", lambda: [(1, "init", "init")])
    monkeypatch.setattr(u, "_tmux_busy", lambda names: "codex" in names)
    monkeypatch.setattr(u, "_run", lambda *a, **kw: pytest.fail("starting worker must defer"))
    assert u.update_tool("codex")["managed_worker"] is True


@pytest.mark.parametrize("dead,expected", [("0", True), ("1", False)])
def test_tmux_scan_uses_agent_marker_and_ignores_dead_panes(monkeypatch, dead, expected):
    monkeypatch.setattr(shutil_for_test := u.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: completed(stdout=f"codex\t{dead}\tzsh\n"))
    assert u._tmux_busy({"codex"}) is expected
