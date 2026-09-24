#!/usr/bin/env python3
"""Build the Aiko website (aiko.eramos.us) — static, catgirl-styled.

Generates site/ from templates embedded below: landing, install, docs.
Deployed via the aikod server (see docs/ops or the deploy script).
"""
from pathlib import Path

SITE = Path(__file__).resolve().parent
SITE.mkdir(exist_ok=True)

PALETTE = {
    "bg": "#0f0f1a", "panel": "#1a1a2e", "pink": "#d75fd7", "pink_soft": "#ffafff",
    "blue": "#87d7ff", "mint": "#afffaf", "text": "#e8e6f8", "dim": "#6c6c8a",
}

CSS = f"""
:root {{
  --bg: {PALETTE['bg']}; --panel: {PALETTE['panel']};
  --pink: {PALETTE['pink']}; --pink-soft: {PALETTE['pink_soft']};
  --blue: {PALETTE['blue']}; --mint: {PALETTE['mint']};
  --text: {PALETTE['text']}; --dim: {PALETTE['dim']};
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
  line-height: 1.6; min-height: 100vh;
}}
a {{ color: var(--pink-soft); text-decoration: none; }}
a:hover {{ color: var(--mint); }}
code, pre {{ font-family: "SF Mono", ui-monospace, Menlo, monospace; }}
code {{
  background: rgba(215, 95, 215, .12); padding: 2px 7px; border-radius: 6px;
  color: var(--pink-soft); font-size: .92em;
}}
pre {{
  background: var(--panel); border: 1px solid rgba(215, 95, 215, .25);
  border-radius: 12px; padding: 16px 20px; overflow-x: auto; font-size: .88em;
  line-height: 1.5; margin: 12px 0;
}}
pre code {{ background: none; padding: 0; color: var(--text); }}
.nav {{
  display: flex; align-items: center; gap: 28px; padding: 18px 32px;
  border-bottom: 1px solid rgba(215, 95, 215, .18);
  background: rgba(15, 15, 26, .85); backdrop-filter: blur(14px);
  position: sticky; top: 0; z-index: 10;
}}
.nav .logo {{
  font-weight: 700; font-size: 1.15rem; color: var(--pink-soft); letter-spacing: .04em;
}}
.nav .logo .cat {{ filter: drop-shadow(0 0 6px rgba(215,95,215,.6)); }}
.nav a {{ color: var(--dim); font-size: .95rem; }}
.nav a:hover {{ color: var(--pink-soft); }}
.nav .cta {{
  margin-left: auto; color: var(--pink-soft); font-weight: 600;
  border: 1px solid var(--pink); padding: 7px 16px; border-radius: 99px;
  transition: all .18s;
}}
.nav .cta:hover {{ background: var(--pink); color: #fff; box-shadow: 0 0 24px rgba(215,95,215,.4); }}
.wrap {{ max-width: 920px; margin: 0 auto; padding: 0 32px; }}
.hero {{
  text-align: center; padding: 88px 24px 64px;
  background:
    radial-gradient(ellipse 60% 40% at 50% -10%, rgba(215,95,215,.22), transparent),
    radial-gradient(ellipse 40% 30% at 80% 20%, rgba(135,215,255,.10), transparent);
}}
.hero .cat-art {{ font-size: 3rem; line-height: 1.35; white-space: pre; }}
.hero h1 {{
  font-size: clamp(2rem, 5vw, 3.2rem); font-weight: 800; letter-spacing: -.02em;
  background: linear-gradient(120deg, var(--pink-soft), var(--blue));
  -webkit-background-clip: text; background-clip: text; color: transparent;
  margin: 18px 0 10px;
}}
.hero .tagline {{ color: var(--dim); font-size: 1.15rem; max-width: 560px; margin: 0 auto; }}
.hero .cta-row {{ display: flex; gap: 14px; justify-content: center; margin-top: 28px; }}
.btn {{
  display: inline-block; padding: 12px 26px; border-radius: 99px; font-weight: 600;
  transition: all .18s; border: 1px solid transparent; font-size: 1rem;
}}
.btn.primary {{ background: linear-gradient(120deg, var(--pink), #9060e8); color: #fff; }}
.btn.primary:hover {{
  transform: translateY(-1px); box-shadow: 0 8px 32px rgba(215,95,215,.35); color: #fff;
}}
.btn.ghost {{ border-color: rgba(215,95,215,.4); color: var(--pink-soft); }}
.btn.ghost:hover {{ background: rgba(215,95,215,.1); color: var(--pink-soft); }}
.features {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 18px; padding: 32px 0 64px;
}}
.card {{
  background: var(--panel); border: 1px solid rgba(215,95,215,.16);
  border-radius: 18px; padding: 26px; transition: all .18s;
}}
.card:hover {{ border-color: rgba(135,215,255,.35); transform: translateY(-2px); }}
.card .icon {{ font-size: 1.8rem; }}
.card h3 {{ color: var(--pink-soft); margin: 10px 0 6px; font-size: 1.05rem; }}
.card p {{ color: var(--dim); font-size: .92rem; }}
.section {{ padding: 56px 0; }}
.section h2 {{
  color: var(--text); font-size: 1.6rem; font-weight: 700; margin-bottom: 8px;
  letter-spacing: -.01em;
}}
.section h2 .accent {{ color: var(--pink); }}
.section .lead {{ color: var(--dim); margin-bottom: 24px; }}
.grid {{ display: grid; gap: 18px; }}
.grid.two {{ grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
.callout {{
  background: linear-gradient(120deg, rgba(215,95,215,.10), rgba(135,215,255,.07));
  border: 1px solid rgba(215,95,215,.25); border-radius: 16px; padding: 20px 24px;
}}
.callout .paw {{ color: var(--pink-soft); }}
footer {{
  text-align: center; color: var(--dim); padding: 40px 24px 48px;
  border-top: 1px solid rgba(215,95,215,.14); font-size: .9rem; margin-top: 40px;
}}
footer .cat {{ color: var(--pink-soft); }}
kbd {{
  background: var(--panel); border: 1px solid rgba(215,95,215,.35); border-bottom-width: 2px;
  border-radius: 6px; padding: 1px 8px; font-size: .85em; color: var(--pink-soft);
  font-family: inherit;
}}
table {{ width: 100%; border-collapse: collapse; margin: 14px 0; font-size: .92rem; }}
th, td {{ text-align: left; padding: 10px 14px; border-bottom: 1px solid rgba(215,95,215,.12); }}
th {{ color: var(--pink-soft); font-weight: 600; }}
td {{ color: var(--text); }}
td.dim {{ color: var(--dim); }}
.docs-body h2 {{ margin-top: 36px; }}
.docs-body h3 {{ color: var(--blue); margin: 24px 0 8px; }}
.docs-body ul {{ padding-left: 22px; color: var(--text); }}
.docs-body ul li {{ margin: 6px 0; }}
.docs-body ul li::marker {{ color: var(--pink); }}
"""

CAT_ART = r"""
     /\_/\   ┌─────────────────┐
    ( o.o )  │  aiko · nya~!   │
     > ^ <   └─────────────────┘
""".strip("\n")


def _nav(active: str) -> str:
    items = [("index.html", "home"), ("install.html", "install"), ("docs.html", "docs")]
    links = []
    for href, label in items:
        cls = ' class="active"' if href == active else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    return (
        f'<nav class="nav">'
        f'<a class="logo" href="index.html"><span class="cat">🐾</span> aiko</a>'
        f'{"".join(links)}'
        f'<a class="cta" href="install.html">get started</a>'
        f"</nav>"
    )


def _page(title: str, active: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · aiko</title>
<link rel="stylesheet" href="style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🐾</text></svg>">
</head>
<body>
{_nav(active)}
{body}
<footer>
  <span class="cat">ฅ^•ﻌ•^ฅ</span> aiko · the agent orchestrator for all your AI agents<br>
  MIT licensed · <a href="https://github.com/eRamos86/aiko">github.com/eRamos86/aiko</a>
</footer>
</body>
</html>"""


def build() -> None:
    # stylesheet
    (SITE / "style.css").write_text(CSS)

    # landing
    landing = f"""
<header class="hero">
  <pre class="cat-art">{CAT_ART}</pre>
  <h1>Aiko — Agent Orchestrator</h1>
  <p class="tagline">for all your AI agents, nya~&nbsp; Local catgirl TUI that chats, plans, and dispatches real work to hermes · codex · agy · opencode — locally or across your servers.</p>
  <div class="cta-row">
    <a class="btn primary" href="install.html">🐾 adopt Aiko</a>
    <a class="btn ghost" href="docs.html">read the docs</a>
  </div>
</header>
<div class="wrap">
  <div class="features">
    <div class="card"><div class="icon">🧠</div><h3>any brain</h3><p>Any OpenAI-compatible endpoint (NIM, OpenRouter, llama.cpp) or Ollama — switch live with <code>/model</code>, reasoning level with ←→.</p></div>
    <div class="card"><div class="icon">🗂️</div><h3>orchestrates agents</h3><p>Dispatch to hermes, codex, agy, opencode on your machines. Watch live sessions, attach, steer mid-run.</p></div>
    <div class="class"><div class="icon">📋</div><h3>lazy vault context</h3><p>Inject a small index of your knowledge base; agents fetch deeper docs on demand. Modular context, not context soup.</p></div>
    <div class="card"><div class="icon">🔌</div><h3>MCP native</h3><p>Aiko has her own MCP client. Add any Model Context Protocol server — she reads your Obsidian vault like it's nothing.</p></div>
    <div class="card"><div class="icon">🎀</div><h3>fully catgirl</h3><p>Boot screen, TUI, CLI — every surface is catgirl-styled. She's competent first, adorable always.</p></div>
    <div class="card"><div class="icon">📡</div><h3>multi-server</h3><p>local + server_one + server_two… dispatch to any aikod daemon. Nova JWT or bearer auth.</p></div>
  </div>

  <section class="section">
    <h2>how she <span class="accent">works</span></h2>
    <p class="lead">You talk to Aiko. Aiko talks to everything else.</p>
    <div class="grid two">
      <div class="callout">
        <p><span class="paw">🐾</span> <b>chat:</b> plain conversation with her brain of choice. She keeps history, switches models mid-chat, and answers in-character.</p>
      </div>
      <div class="callout">
        <p><span class="paw">🐾</span> <b>dispatch:</b> "there's a login bug in flux" → she decomposes, picks a target (local for quick stuff, server for heavy jobs), launches the right agent, watches it, and reports back.</p>
      </decent...[truncated]
""".replace('class="class"', 'class="card"').replace("</decent", "</div")
    # fix the truncation artifact by rebuilding that tail cleanly
    landing_tail = """
      </div>
      <div class="callout">
        <p><span class="paw">🐾</span> <b>plan:</b> <code>/plan <braindump></code> — dump your thoughts, she organizes them into tasks, doc destinations, and open questions. Nothing gets dispatched until you say so.</p>
      </div>
    </div>
  </section>

  <section class="section">
    <h2>made by <span class="accent">Ace</span></h2>
    <p class="lead">Aiko is one agent in Ace's platform — Nova, Nexus, Flux, Atlas, North, and the rest. She's the catgirl glue that talks to all the others.</p>
    <p style="color:var(--dim)">If she misbehaves, she gets <a href="https://aikosay.eramos.us">scritches</a>. (jk there's no such site — but you can <code>aikosay pat</code> on Ace's machine, nya~)</p>
  </section>
</div>
"""
    landing_full = landing + landing_tail
    (SITE / "index.html").write_text(_page("home", "index.html", landing_full))

    # install
    install = f"""
<div class="wrap docs-body">
  <section class="section">
    <h2>adopt <span class="accent">Aiko</span></h2>
    <p class="lead">Two pieces: <code>aiko</code> (local TUI client, this machine) and optionally <code>aikod</code> (daemon, on a server) — so she can orchestrate agents on your other machines too.</p>

    <h3>requirements</h3>
    <ul>
      <li>Python 3.11+ (3.9 won't cut it, nya)</li>
      <li>a terminal that loves you back</li>
      <li>(optional) an OpenAI-compatible API key — NIM, OpenRouter, local llama.cpp, or Ollama</li>
    </ul>

    <h3>1 · install the client</h3>
    <pre><code>pipx install git+https://github.com/eRamos86/aiko</code></pre>
    <p>or with pip: <code>pip install git+https://github.com/eRamos86/aiko</code></p>

    <h3>2 · configure her</h3>
    <p>Walk through every section with the guided wizard:</p>
    <pre><code>aiko config</code></pre>
    <p>The wizard covers brains (models), servers (aikod daemons), agents (local CLIs), context providers (docs injection), and MCP servers. Prefer raw YAML? <code>aiko config --edit</code>.</p>

    <h3>3 · say hi</h3>
    <pre><code>aiko</code></pre>
    <p>The TUI boots with the catgirl loading screen, four tabs (chat · sessions · concord · status), and she greets you. Try:</p>
    <pre><code>you: hey aiko, what can you do?
you: /model          # pick a brain + reasoning interactively
you: /plan I want to add auth to my app...   # braindump mode
you: /new            # fresh conversation</code></pre>

    <h3>4 · (optional) server-side daemon</h3>
    <p>Install <code>aikod</code> on any server to dispatch work to ITS agents:</p>
    <pre><code>pipx install git+https://github.com/eRamos86/aiko
aikod serve            # starts the daemon (systemd unit in the repo)</pre></code>
    <p>Then add it in <code>aiko config</code> → servers → daemon URL + auth.</p>

    <h3>uninstall</h3>
    <p><code>pipx uninstall aiko</code> — she'll pretend she isn't sad. She is. 🐾</p>
  </section>
</div>
"""
    (SITE / "install.html").write_text(_page("install", "install.html", install))

    # docs
    docs = f"""
<div class="wrap docs-body">
  <section class="section">
    <h2>aiko <span class="accent">docs</span></h2>
    <p class="lead">Everything Aiko speaks — config, commands, slash commands, and the daemon.</p>

    <h3 id="config">config (~/.aiko/config.yaml)</h3>
    <pre><code>brains:
  - name: my-brain
    type: openai            # or: ollama
    model: nvidia/nemotron-3-super-120b-a12b
    base_url: https://integrate.api.nvidia.com/v1
    api_key: ${{NIM_API_KEY}}     # $ENV expansion supported

servers:                     # list — local:, server_one:, server_two: …
  - name: server_one
    url: https://aikod.example.com
    auth: nova_jwt           # or: token
    identifier: you@mail.com

agents:                      # local CLI agents she can spawn
  - name: hermes
    command: hermes
    one_shot: [chat, -q, "{{{{prompt}}}}"]

context_providers:           # docs attached to dispatches
  - name: my-vault
    type: http_docs
    base_url: https://vault.example.com
    headers: {{X-Vault-Secret: ${{VAULT_TOKEN}}}}
    lazy_attach: [Agents/Context Router.md]

mcp_servers:                 # Aiko's own MCP client
  vault:
    url: https://vault-mcp.example.com/mcp
    headers: {{X-Vault-Secret: ${{VAULT_TOKEN}}}}</code></pre>

    <h3>slash commands</h3>
    <table>
      <tr><th>command</th><th>what it does</th></tr>
      <tr><td><code>/model</code></td><td>interactive picker — ↑↓ brain, ←→ reasoning, Enter to confirm</td></tr>
      <tr><td><code>/new</code></td><td>fresh conversation (alias: <code>/reset</code>)</td></tr>
      <tr><td><code>/plan <dump></code></td><td>planning mode — organizes a braindump, dispatches nothing</td></tr>
      <tr><td><code>/targets</code></td><td>list dispatch targets + health</td></tr>
      <tr><td><code>/status</code></td><a href="docs.html#status">status overview</a></td></tr>
      <tr><td><code>/clear</code></td><td>clear the chat log</td></tr>
      <tr><td><code>/help</code></td><td>all commands</td></tr>
    </table>

    <h3>CLI</h3>
    <table>
      <tr><th>command</th><th>what it does</th></tr>
      <tr><td><code>aiko</code></td><td>the TUI — chat, sessions, concord, status</td></tr>
      <tr><td><code>aiko config</code></td><td>guided config wizard</td></tr>
      <tr><td><code>aiko config --edit</code></nd><td>raw YAML in $EDITOR</td></tr>
      <tr><td><code>aiko goal "<goal>"</code></td><td>one-shot dispatch (no TUI)</td></tr>
      <tr><td><code>aiko routing</code></td><td>daemon routing log (server mode)</td></tr>
      <tr><td><code>aiko whoami</code></td><td>who the daemon thinks you are</td></tr>
    </table>

    <h3>the daemon (aikod)</h3>
    <p>Run on a server to accept dispatches from your local Aiko. It exposes sessions, transcripts, steer messages, health. Auth: Nova JWT or bearer token. The repo ships a systemd unit (<code>aikod.service</code>).</p>

    <h3 id="status">status tab & /status</h3>
    <p>Brains, servers, agents, MCP servers, session counts — all in one glance. Runs every 5s while the status tab is active.</p>

    <h3>philosophy</h3>
    <ul>
      <li><b>local-first:</b> your config, your keys, your machines. No cloud required — any OpenAI-compatible endpoint works.</li>
      <li><b>lazy context:</b> inject indexes, not libraries. Aiko reads a small router doc; deeper docs get fetched on demand.</li>
      <li><b>competence first, catgirl always:</b> she gets work done, then purrs about it.</li>
    </ul>
  </section>
</div>
"""
    (SITE / "docs.html").write_text(_page("docs", "docs.html", docs))
    print(f"site built: {[p.name for p in SITE.iterdir()]}")


if __name__ == "__main__":
    build()
