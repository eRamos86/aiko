"""aiko CLI — `aiko` launches the TUI; subcommands for everything else."""
from pathlib import Path

import typer

app = typer.Typer(help="Aiko — The Agent Orchestrator for all your AI Agents 🐾",
                  no_args_is_help=False)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """🐾 Aiko — launch the TUI when run with no command."""
    if ctx.invoked_subcommand is None:
        from .tui import run_tui
        run_tui()


@app.command()
def login(identifier: str = typer.Option(..., "-u", help="email or username"),
          password: str = typer.Option(..., "-p", hide_input=True),
          base: str = typer.Option("https://login.eramos.us", help="auth base url")):
    """Log in (Nova JWT) — for server auth configured as nova_jwt."""
    from .auth import login as do_login
    user = do_login(identifier, password, base)
    typer.echo(f"Logged in as {user['email']} 🐾")


@app.command()
def audit_docs(
    max_docs: int = typer.Option(40, "--max", help="max docs to scan"),
    no_fix: bool = typer.Option(False, "--no-fix", help="flag only, don't fix"),
):
    """Trigger the standalone docs-auditor service (via aikod proxy)."""
    import httpx
    from .config_wizard import load_config
    cfg = load_config()
    servers = cfg.get("servers", [])
    if not servers:
        # fall back to hitting the local service directly
        from .plugins import docsauditor
        if not docsauditor.health():
            typer.echo("docs-auditor service not running 🐾 — start it with `docsauditor serve`")
            raise typer.Exit(1)
        data = docsauditor.audit_docs(max_docs=max_docs, auto_fix=not no_fix)
    else:
        srv = servers[0]
        token = srv.get("token", "")
        r = httpx.get(f"{srv['url'].rstrip('/')}/audit/docs",
                      params={"max_docs": max_docs, "auto_fix": not no_fix},
                      headers={"Authorization": f"Bearer {token}"},
                      timeout=httpx.Timeout(600, connect=10))
        data = r.json()
    if "error" in data:
        typer.echo(f"audit error: {data['error']}")
        raise typer.Exit(1)
    typer.echo(f"🐾 scanned {data['scanned']} docs "
               f"({', '.join(data['scope'])}): "
               f"{data['fixed']} fixed, {data['needs_ace']} need you")
    for f in data.get("fixes", []):
        mark = {"fixed": "✓", "needs-ace": "❓", "flagged": "⚠"}.get(f["action"], "✗")
        typer.echo(f"  {mark} {f['path']}")


@app.command()
def status():
    """Targets, sessions, and daemon health."""
    from .backends import get_backend
    b = get_backend()
    for t in b.list_targets():
        typer.echo(f"▶ {t.get('target')}: {t.get('status', 'agents: ' + str(t.get('agents', [])))}")
    sessions = b.list_sessions()
    if sessions:
        typer.echo(f"\n{len(sessions)} session(s):")
        for s in sessions:
            typer.echo(f"  {s['id'][:14]}  {s.get('provider', '-'):<12} "
                       f"{s.get('session_state', '-'):<8} {s.get('title', '')[:50]}")


@app.command()
def goal(text: str, target: str = typer.Option(None, "--target", "-t",
                                               help="local or server name")):
    """Dispatch a goal without the TUI."""
    from .backends import get_backend
    result = get_backend().dispatch(text, target)
    typer.echo(f"Dispatched: {result}")


@app.command()
def model():
    """List configured brains (switch in the TUI with /model)."""
    from .brain import list_brains
    brains = list_brains()
    if not brains:
        typer.echo("No brains configured — see ~/.aiko/config.yaml, nya")
        return
    for b in brains:
        kind = b.get("type", "?")
        model = b.get("model", "?")
        typer.echo(f"  {b['name']:<24} {kind:<18} {model}")


@app.command()
def config(
    edit: bool = typer.Option(False, "--edit", "-e", help="open the raw YAML for editing instead of the wizard"),
):
    """Guided config wizard (or --edit for the raw YAML)."""
    from .config import CONFIG_PATH, write_default_if_missing
    if edit:
        if write_default_if_missing():
            typer.echo(f"Wrote default config to {CONFIG_PATH} 🐾")
        typer.echo(f"Opening {CONFIG_PATH} in $EDITOR…")
        import os
        import subprocess
        subprocess.run([os.environ.get("EDITOR", "vim"), str(CONFIG_PATH)])
        return
    from .config_wizard import run_wizard
    run_wizard()


@app.command()
def routing(tail: bool = typer.Option(False, "-f")):
    """Show routing decisions from the daemon (server mode)."""
    log = Path.home() / ".aiko" / "routing.log"
    if not log.exists():
        typer.echo("No routing log yet.")
        return
    if tail:
        import subprocess
        subprocess.run(["tail", "-f", str(log)])
    else:
        typer.echo(log.read_text()[-4000:])


@app.command()
def usage():
    """Show the usage economy: budgets, consumption, cooldowns."""
    from .usage import snapshot_all
    snap = snapshot_all()
    if not snap:
        typer.echo("(no usage recorded yet, nya)")
        return
    for key, info in snap.items():
        mark = " ⏸COOLDOWN" if info["cooldown_s"] else ""
        typer.echo(f"🪙 {key}{mark}")
        for b in info["budgets"]:
            typer.echo(f"    {b['unit']}: {b['used']}/{b['cap']}"
                       f"  (resets in {int(b['resets_in'])}s)")
        if info["raw_24h"]:
            raw = " · ".join(f"{u}:{n}" for u, n in sorted(info["raw_24h"].items()))
            typer.echo(f"    24h raw: {raw}")


@app.command()
def usage_set(key: str, unit: str, cap: int, window: str):
    """Set a budget cap: aiko usage-set nim requests 40 5h."""
    from .usage import set_budget
    result = set_budget(key, unit, cap, window)
    typer.echo(f"✓ {result['key']}: {result['unit']} cap={result['cap']} "
               f"window={result['window']}s, nya~")


@app.command("update")
def update_command(
    wheel: Path | None = typer.Option(None, "--wheel", help="Install this built wheel on all configured hosts"),
    local_only: bool = typer.Option(False, "--local-only", help="Update only this installation"),
):
    """Stage a new release and update configured servers; running workers stay alive."""
    import json
    from .updates import update
    try:
        result = update(wheel=wheel, local_only=local_only)
        typer.echo(json.dumps(result, indent=2, default=str))
        if result.get("status") in ("failed", "partial", "error"):
            raise typer.Exit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        typer.echo(f"Update failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command("update-tool")
def update_tool_command(name: str, target: str = typer.Option("local", "--target", "-t")):
    """Update an AI tool, or queue it until all of its sessions are idle."""
    import json
    from .updates import update_tool
    try:
        result = update_tool(name, target)
        typer.echo(json.dumps(result, indent=2, default=str))
        if result.get("status") in ("failed", "error"):
            raise typer.Exit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)


@app.command("updates-pending")
def updates_pending():
    """Retry queued tool updates that are now idle. The TUI also retries periodically."""
    import json
    from .updates import run_pending_updates
    typer.echo(json.dumps(run_pending_updates(), indent=2, default=str))


@app.command("open")
def open_agent(
    agent: str,
    repo: str = typer.Option(..., "--repo", "-C", help="Working directory on the selected host"),
    target: str = typer.Option("local", "--target", "-t"),
    detached: bool = typer.Option(False, "--detached", help="Launch without attaching this terminal"),
):
    """Open a persistent independent AI terminal; Ctrl+B, D detaches."""
    import subprocess
    import json
    from .session_manager import SessionManager
    try:
        manager = SessionManager()
        result = manager.open(agent, repo, target)
        typer.echo(json.dumps(result, indent=2, default=str))
        if not detached:
            subprocess.run(manager.attach_command(result["id"], target), check=False)
    except (ValueError, RuntimeError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)


@app.command("attach")
def attach_agent(session_id: str, target: str = typer.Option("local", "--target", "-t")):
    """Return to a persistent AI terminal."""
    import subprocess
    from .session_manager import SessionManager
    try:
        subprocess.run(SessionManager().attach_command(session_id, target), check=True)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)


@app.command("sessions")
def direct_sessions():
    """List independent terminals across configured hosts."""
    import json
    from .session_manager import SessionManager
    typer.echo(json.dumps(SessionManager().list(), indent=2, default=str))


@app.command("skills")
def skills(name: str | None = typer.Argument(None)):
    """List available skills or read one by name."""
    from .skills import list_skills, read_skill
    if name:
        typer.echo(read_skill(name))
    else:
        for skill in list_skills():
            typer.echo(f"{skill['name']}: {skill['description']}")


@app.command("version")
def version():
    """Show installed package version and runtime location."""
    from importlib.metadata import version as package_version
    import sys
    typer.echo(f"Aiko {package_version('aiko')}\n{sys.prefix}")


if __name__ == "__main__":
    app()
