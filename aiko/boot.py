"""Aiko boot/loading screen — shows what's being wired, catgirl-styled (ADR-011)."""
import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

BOOT_STEPS = [
    ("🐾", "waking Aiko up…", 0.15),
    ("🧠", "loading brains from ~/.aiko/config.yaml…", 0.3),
    ("🔌", "checking context providers…", 0.25),
    ("🖥️", "probing local agents (hermes · codex · agy)…", 0.35),
    ("📡", "pinging servers…", 0.4),
    ("📋", "restoring sessions view…", 0.2),
    ("🎀", "fluffing tail…", 0.15),
]

CAT_ART = r"""
     /\_/\    🐾  A I K O  🐾
    ( •ᴗ• )   Agent Orchestrator
    /  >💻    for all your AI Agents
"""


class BootScreen(Screen):
    """Animated boot log; dismisses itself when all steps complete."""
    CSS = """
    #boot { align: center middle; }
    #boot_art { color: #d75fd7; text-align: center; padding-bottom: 1; }
    #boot_log { width: 56; height: auto; border: round #d75fd7;
                background: #1a1a2e; padding: 1 2; }
    .step { color: #87d7ff; }
    .step-done { color: #afffaf; }
    .step-paw { color: #ffafff; }
    #boot_hint { color: #6c6c6c; text-align: center; padding-top: 1; }
    """

    def __init__(self, step_results: list[tuple[str, str]] | None = None):
        super().__init__()
        # step_results: (label, result_text) filled by the app during boot
        self.step_results = step_results or []

    def compose(self) -> ComposeResult:
        with Vertical(id="boot"):
            yield Static(CAT_ART, id="boot_art")
            yield Static("", id="boot_log")
            yield Static("nyaa~ almost there…", id="boot_hint")

    def on_mount(self) -> None:
        self._log = self.query_one("#boot_log", Static)
        asyncio.create_task(self._run())

    async def _run(self) -> None:
        import time
        lines = []
        for emoji, label, delay in BOOT_STEPS:
            lines.append(f"[b] {emoji} [/b] {label}")
            self._log.update("\n".join(lines))
            await asyncio.sleep(delay)
        # append any real results the app gathered
        for label, result in self.step_results:
            lines.append(f" [mint]✓[/mint] {label}: {result}")
        lines.append("[b pink] ✓ Aiko is ready, nya~![/b pink]")
        self._log.update("\n".join(lines))
        await asyncio.sleep(0.5)
        self.app.pop_screen()
