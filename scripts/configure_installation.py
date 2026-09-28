"""Configure an existing personal installation without replacing its credentials."""
import argparse
from pathlib import Path
import shutil
import time

from aiko.config import CONFIG_PATH, load_config, save_config


def configure(source, server=None, ssh_host=None, remote_home=None):
    source = Path(source).expanduser().resolve()
    if not (source / ".git").exists():
        raise ValueError("source must be the canonical Git checkout")
    cfg = load_config()
    if CONFIG_PATH.exists():
        backup = CONFIG_PATH.with_name(f"config.before-install-{time.time_ns()}.yaml")
        shutil.copy2(CONFIG_PATH, backup)
        backup.chmod(0o600)
    settings = cfg.setdefault("updates", {})
    settings["source"] = str(source)
    settings.setdefault("install_root", str(Path.home() / ".local/share/aiko"))
    if server:
        servers = cfg.setdefault("servers", [])
        entry = next((s for s in servers if s["name"] == server), None)
        if entry is None:
            entry = {"name": server}
            servers.append(entry)
        entry["ssh_host"] = ssh_host
        remote = str(Path(remote_home) / ".local/share/aiko")
        entry["install_root"] = remote
        entry["restart_command"] = [f"{remote}/current/venv/bin/python", "-m", "aiko.service", "restart"]
        cfg["default_target"] = server
    agents = cfg.setdefault("agents", [])
    for name, command in (("opencode", "opencode"), ("codex", "codex"), ("hermes", "hermes")):
        entry = next((a for a in agents if a["name"] == name), None)
        if entry is None:
            entry = {"name": name, "command": command}
            agents.append(entry)
        entry.setdefault("interactive", [entry["command"]])
        if name == "hermes":
            entry.setdefault("process_names", ["hermes", "hermes_cli.main"])
        if name == "opencode":
            entry.setdefault("one_shot", ["run", "{prompt}"])
    cfg.setdefault("skills", {}).setdefault("directories", [str(Path.home() / ".aiko/skills")])
    save_config(cfg)
    CONFIG_PATH.chmod(0o600)
    print(f"Configured source: {source}")
    if server:
        print(f"Configured server: {server} via {ssh_host}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--server")
    parser.add_argument("--ssh-host")
    parser.add_argument("--remote-home")
    args = parser.parse_args()
    if args.server and not (args.ssh_host and args.remote_home):
        parser.error("--server requires --ssh-host and --remote-home")
    configure(args.source, args.server, args.ssh_host, args.remote_home)
