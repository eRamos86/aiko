<div align="center">

```
  /\_/\   Aiko
 ( •ᴗ• )  The Agent Orchestrator
 / >💻    for all your AI Agents
```

**nya~** • your agents, orchestrated by a catgirl • **nya~**

[![License: MIT](https://img.shields.io/badge/license-MIT-pink)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)

</div>

---

Aiko is a terminal-native orchestrator for AI coding agents. Text her a goal —
*"there's a login bug in flux"* — and she decomposes it, dispatches the work to
your agents (Codex, Claude Code, Hermes, Antigravity, whatever you configure),
watches the results, and coordinates follow-ups (fix → review → document).
She's a catgirl, nya~.

## Features

- 💬 **Chat-centric TUI** — talk to Aiko like any other agent; she orchestrates the rest
- 🎯 **Multi-target dispatch** — local agents, one server, or many; Aiko picks (or you say "on the server")
- 🧠 **Any brain, any provider** — NIM, OpenRouter, OpenAI, local Ollama — anything OpenAI-compatible, config-driven
- 📋 **Live session attach** — watch any worker's transcript stream, message it mid-run
- 🔌 **Context providers** — optionally attach your docs/knowledge base to worker prompts
- 🐾 **Fully catgirl** — obviously

## Install

```bash
# from source (for now)
git clone https://github.com/eRamos86/aiko.git
cd aiko
uv venv && uv pip install -e ".[dev]"
ln -sf "$(pwd)/.venv/bin/aiko" ~/.local/bin/aiko
aiko   # opens the TUI 🐾
```

## Configure

`~/.aiko/config.yaml` — everything lives here. On first run Aiko writes a
documented default. Key sections:

```yaml
brains:            # what Aiko thinks with — /model switches between them
  - name: nim-nemotron
    type: openai_compatible
    base_url: https://integrate.api.nvidia.com/v1
    api_key: ${NIM_API_KEY}     # env-var expansion supported
    model: nvidia/nemotron-3-super-120b-a12b

servers:           # optional — omit entirely for local-only orchestration
  - name: myserver
    url: https://aikod.example.com
    auth: none|token|nova_jwt

agents:            # local agent binaries Aiko can spawn directly
  - name: codex
    command: codex
    one_shot: ["exec", "--skip-git-repo-check", "{prompt}"]
```

## The two shapes

**Local-only** (no server needed): configure `brains` + `agents`, and Aiko
orchestrates everything on your machine.

**With servers**: run the `aikod` daemon on any box (`aikod --db ~/.aikod/aikod.db`),
add it to `servers:`, and Aiko dispatches across machines — heavy work to the
server, quick stuff local. `aikod` has its own router (YAML floor + observed
signal modifiers) that picks provider+model per task.

## TUI keys & slash commands

| key | action |
|---|---|
| `1`–`4` | Chat / Sessions / Concord / Status tabs |
| `/model [name]` | switch brain (or list) |
| `/targets` | show local + servers with health |
| `/status` | quick session overview |
| `/reset` `/clear` `/help` | the usual |

## License

MIT — go build things, nya~ 🐾
