"""Aiko configuration — ~/.aiko/config.yaml (v1.0 schema).

Fully open + user-configurable:
  brains:      arbitrary list of OpenAI-compatible providers (+ ollama local).
               /model lists these. Each: name, provider(base_url+key) or ollama, model.
  backend:     'local' (orchestrate only this machine's agents) or 'servers'.
  servers:     daemon fleet — name, url, auth (none | token | nova_jwt).
  agents:      local agent binaries the orchestrator can spawn directly.
  context:     optional context_providers (e.g. a docs vault HTTP API). Plugin,
               not core — Aiko works fine with none.
"""
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / ".aiko"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

DEFAULT_CONFIG = """\
# ╭──────────────────────────────────────────────────────────╮
# │  🐾  Aiko — The Agent Orchestrator for all your AI Agents  │
# ╰──────────────────────────────────────────────────────────╯
# Edit me at ~/.aiko/config.yaml — everything is optional except you. :3

# ── Brains: what the orchestrator thinks with ────────────────
# Any OpenAI-compatible endpoint works: nvidia NIM, OpenRouter, OpenAI,
# Together, Groq, a local vLLM/ollama, anything with a /chat/completions.
brains:
  - name: nim-nemotron-super
    type: openai_compatible
    base_url: https://integrate.api.nvidia.com/v1
    api_key: "${NIM_API_KEY}"        # ${ENV_VAR} expansion supported
    model: nvidia/nemotron-3-super-120b-a12b
  - name: ollama-local
    type: ollama
    endpoint: http://localhost:11434
    model: gpt-oss:20b
default_brain: nim-nemotron-super

# ── Backend: where work runs ─────────────────────────────────
# local   = orchestrate agents on THIS machine only (no server needed!)
# servers = dispatch work to one or more aikod daemons (below)
backend: servers

servers:
  - name: poopmachine
    url: https://aikod.eramos.us
    auth: nova_jwt                  # none | token | nova_jwt
    # token auth: set AIKO_TOKEN_<NAME> env or auth_token: here
    # nova_jwt: `aiko login` (Nova) or auth_token + auth_refresh_token

# ── Local agents (spawned directly, no server involved) ──────
# Used in local mode AND available to the orchestrator anytime.
agents:
  - name: hermes
    command: hermes
    one_shot: ["chat", "-q", "{prompt}"]
  - name: codex
    command: codex
    one_shot: ["exec", "--skip-git-repo-check", "{prompt}"]
  - name: antigravity
    command: agy
    one_shot: ["--output-format", "text", "--print={prompt}"]

# ── Context providers (optional plugin — Aiko runs fine without) ──
# Aiko attaches each provider's docs to worker prompts when set.
# type http_docs: any read API returning {"content": ...} for a path.
context_providers:
  - name: obsidian-vault
    type: http_docs
    base_url: https://vault.eramos.us
    headers:
      X-Vault-Secret: "${VAULT_API_SECRET}"
    attach:
      - Agents/Agents.md
      - Agents/Shared-Context.md
"""


def load_config() -> dict:
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except FileNotFoundError:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_config()
    existing.update(cfg)
    CONFIG_PATH.write_text(yaml.safe_dump(existing, sort_keys=False))


def expand(value) -> str:
    """Expand ${ENV_VAR} in string values."""
    import os
    import re
    if not isinstance(value, str):
        return value
    def sub(m):
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{([A-Z0-9_]+)\}", sub, value)


def write_default_if_missing() -> bool:
    """On first run: write the default config. Returns True if written."""
    if CONFIG_PATH.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(DEFAULT_CONFIG)
    CONFIG_PATH.chmod(0o600)
    return True
