"""aiko config — interactive guided setup wizard.

Walks through every configuration section in ~/.aiko/config.yaml:
brains, servers, agents, context_providers, mcp_servers. Existing values
are offered as defaults; ^C keeps your config untouched.
"""
import webbrowser
from pathlib import Path

import typer

from .config import CONFIG_PATH, load_config, save_config


def _ask(prompt: str, default: str | None = None) -> str:
    """Prompt with visible default; empty input keeps the default."""
    shown = f"{prompt} [{default}] " if default else f"{prompt} "
    while True:
        val = input(shown).strip()
        if val:
            return val
        if default is not None:
            return default
        print("  please enter something, or ctrl-c to keep your config as-is")


def _ask_bool(prompt: str, default: bool) -> bool:
    d = "Y/n" if default else "y/N"
    val = input(f"{prompt} [{d}] ").strip().lower()
    if not val:
        return default
    return val[0] in ("y", "1", "t")


def _mask(secret: str) -> str:
    return secret[:4] + "…" + secret[-4:] if len(secret) > 8 else "…"


def _wizard_brains(cfg: dict) -> None:
    print("\n== 🧠 brains — models Aiko can think with ==")
    print("   Any OpenAI-compatible endpoint (NIM, OpenRouter, local llama.cpp…)")
    print("   or Ollama. Leave blank to keep existing.\n")
    brains = cfg.setdefault("brains", [])
    if brains:
        print("   configured:")
        for b in brains:
            print(f"     - {b.get('name')}: {b.get('model')} @ {b.get('base_url', 'ollama')[:40]}")
    if not _ask_bool("   add another brain?", default=not brains):
        return
    while True:
        name = _ask("   name for this brain (e.g. nim-nemotron-super)")
        kind = input("   type [openai] or [ollama]? [openai] ").strip() or "openai"
        brain = {"name": name, "type": kind}
        if kind == "ollama":
            brain["model"] = _ask("   ollama model id (e.g. llama3.2)")
            brain["host"] = input("   ollama host [http://localhost:11434] ").strip() or "http://localhost:11434"
        else:
            brain["model"] = _ask("   model id (e.g. nvidia/nemotron-3-super-120b-a12b)")
            brain["base_url"] = _ask("   base_url", "https://integrate.api.nvidia.com/v1")
            key = input("   API key (or $ENV_VAR to read from env): ").strip()
            if key:
                brain["api_key"] = key
        brains.append(brain)
        if not _ask_bool("   add another?", default=False):
            return


def _wizard_servers(cfg: dict) -> None:
    print("\n== 📡 servers — aikod daemons you can dispatch to ==")
    print("   multiple servers supported: local:, server_one:, server_two: …\n")
    # accept list-of-dicts (with `name` keys) OR dict-of-dicts — either shape
    raw = cfg.get("servers")
    servers: dict = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "name" in item:
                servers[item["name"]] = {k: v for k, v in item.items() if k != "name"}
    elif isinstance(raw, dict):
        servers = raw
    if servers:
        print("   configured:")
        for name, s in servers.items():
            print(f"     - {name}: {s.get('url')} ({s.get('auth', '?')})")
    if not _ask_bool("   add a server?", default=not servers):
        return
    while True:
        name = _ask("   server name (e.g. server_one)")
        s = {"url": _ask("   daemon URL (e.g. https://aikod.example.com)")}
        auth = input("   auth type [nova_jwt] or [token]? [nova_jwt] ").strip() or "nova_jwt"
        s["auth"] = auth
        if auth == "nova_jwt":
            print("   → Nova login: identifier + password → JWT (7-day)")
            s["identifier"] = _ask("   nova identifier (email)")
            s["auth_url"] = _ask("   nova auth URL", "https://login.eramos.us/auth/login")
        else:
            tok = input("   bearer token: ").strip()
            if tok:
                s["token"] = tok
        servers[name] = s
        if not _ask_bool("   add another?", default=False):
            return
    # persist back in the same shape we found
    if isinstance(raw, list):
        out = []
        for name, s in servers.items():
            entry = dict(s)
            entry["name"] = name
            out.append(entry)
        cfg["servers"] = out
    else:
        cfg["servers"] = servers


def _wizard_agents(cfg: dict) -> None:
    print("\n== 🖥️ agents — local CLIs Aiko can spawn ==")
    print("   name → binary; discovered ones are offered as defaults.\n")
    from shutil import which
    discovered = {a: which(a) for a in ("hermes", "codex", "agy", "opencode") if which(a)}
    raw = cfg.get("agents")
    agents: list = raw if isinstance(raw, list) else []
    known = {a.get("name") for a in agents if isinstance(a, dict)}
    if agents:
        print("   configured:")
        for a in agents:
            print(f"     - {a.get('name')}: {a.get('command', '?')}")
    if discovered:
        print("   detected on this machine:")
        for a, path in discovered.items():
            mark = "✓" if a in known else " "
            print(f"     {mark} {a} ({path})")
    for name, path in discovered.items():
        if name not in known and _ask_bool(f"   add {name}?", default=True):
            agents.append({"name": name, "command": name})
            known.add(name)
    if _ask_bool("   add another agent manually?", default=False):
        while True:
            nm = _ask("   agent name")
            cmd = _ask("   command/binary")
            agents.append({"name": nm, "command": cmd})
            known.add(nm)
            if not _ask_bool("   add another?", default=False):
                return
    cfg["agents"] = agents


def _wizard_context(cfg: dict) -> None:
    print("\n== 📋 context_providers — docs attached to dispatches ==")
    print("   e.g. your Obsidian vault API. `lazy_attach` injects just a small")
    print("   index doc; agents fetch deeper docs on demand.\n")
    providers = cfg.setdefault("context_providers", [])
    if providers:
        for p in providers:
            print(f"     - {p.get('name', '?')}: {p.get('base_url', '?')}")
            if p.get("lazy_attach"):
                print(f"       lazy_attach: {p['lazy_attach']}")
    if not _ask_bool("   add a provider?", default=not providers):
        return
    while True:
        p = {"name": _ask("   provider name"), "type": "http_docs",
             "base_url": _ask("   base_url (e.g. https://vault.example.com)")}
        hdr_name = _ask("   auth header name", "X-Vault-Secret")
        hdr_val = input("   auth header value: ").strip()
        if hdr_val:
            p["headers"] = {hdr_name: hdr_val}
        mode = input("   lazy (small index doc) or full attach? [lazy] ").strip() or "lazy"
        doc = _ask("   doc path to attach", "Agents/Context Router.md")
        if mode == "lazy":
            p["lazy_attach"] = [doc]
        else:
            p["attach"] = [doc]
        providers.append(p)
        if not _ask_bool("   add another?", default=False):
            return


def _wizard_mcp(cfg: dict) -> None:
    print("\n== 🔌 mcp_servers — Model Context Protocol servers for Aiko ==")
    print("   Aiko calls these tools during chats (her own MCP client).\n")
    mcps = cfg.setdefault("mcp_servers", {})
    if mcps:
        print("   configured:")
        for name, m in mcps.items():
            print(f"     - {name}: {m.get('url')}")
    if not _ask_bool("   add an MCP server?", default=not mcps):
        return
    while True:
        name = _ask("   MCP server name (e.g. vault)")
        m: dict = {"url": _ask("   MCP URL (e.g. https://vault-mcp.example.com/mcp)")}
        hn = input("   auth header name [X-Vault-Secret]: ").strip() or "X-Vault-Secret"
        hv = input("   auth header value: ").strip()
        if hv:
            m["headers"] = {hn: hv}
        mcps[name] = m
        if not _ask_bool("   add another?", default=False):
            return


def run_wizard() -> None:
    """Interactive guided walkthrough of every aiko config section."""
    print("🐾 aiko config — guided setup, nya~")
    print(f"   file: {CONFIG_PATH}\n")
    if not CONFIG_PATH.exists():
        print("   no config yet — we'll create one together!")
    cfg = load_config()
    try:
        _wizard_brains(cfg)
        _wizard_servers(cfg)
        _wizard_agents(cfg)
        _wizard_context(cfg)
        _wizard_mcp(cfg)
    except (KeyboardInterrupt, EOFError):
        print("\n   cancelled — config left untouched, nya")
        return
    save_config(cfg)
    print(f"\n✓ saved to {CONFIG_PATH}")
    n_brains = len(cfg.get("brains", []))
    n_servers = len(cfg.get("servers", {}))
    n_mcps = len(cfg.get("mcp_servers", {}))
    print(f"  🧠 {n_brains} brain(s) · 📡 {n_servers} server(s) · 🔌 {n_mcps} MCP server(s)")
    print("  run `aiko` to chat with everything wired, nya~ ฅ^•ﻌ•^ฅ")


if __name__ == "__main__":
    run_wizard()
