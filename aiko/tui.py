"""Aiko TUI — chat-centric catgirl orchestrator client (ADR-010, FULL catgirl).

Palette = Aiko shell: pink 213, sky 117, mint 120, salmon 203, lavender 141,
gray 245 on dark. Tabs: Chat · Sessions · Concord · Status. Slash commands in
chat: /model /targets /help /clear /reset /status.
"""
import json
import subprocess
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (Button, Footer, Header, Input, ListItem, ListView,
                             RichLog, Select, Static, TabbedContent, TabPane)

from .agent import Orchestrator
from .brain import Brain, list_brains
from .backends import get_backend

ROUTING_LOG = Path.home() / ".aiko" / "routing.log"

CAT_BANNER = r"""
  /\_/\   Aiko — Agent Orchestrator
 ( •ᴗ• )  for all your AI Agents, nya~
 / >💻    type a goal below! ฅ^•ﻌ•^ฅ
"""

HELP_TEXT = """[b pink]Aiko slash commands[/b pink]
  [sky]/model[/sky] [i]<name>[/i]     switch brain (no arg = list)
  [sky]/targets[/sky]          list execution targets (local + servers)
  [sky]/status[/sky]           quick session + target overview
  [sky]/reset[/sky]            fresh conversation (same brain)
  [sky]/clear[/sky]            clear the chat log
  [sky]/help[/sky]             this message

[i gray]keys: 1 chat · 2 sessions · 3 concord · 4 status · ctrl+l concord · q quit[/i gray]"""


class AikoTUI(App):
    CSS = """
    #status_bar { dock: bottom; height: 1; background: #1a1a2e; color: #ffafff; }
    #chat_log { height: 1fr; border: round #d75fd7; }
    #chat_input { dock: bottom; border: tall #d75fd7; }
    #chat_input:focus { border: tall #ff87ff; }
    #brain_bar { dock: top; height: 3; }
    #brain_label { color: #d75fd7; width: auto; padding: 0 1; }
    #session_list { height: 30%; border: round #d75fd7; }
    #attach_log { height: 1fr; border: round #87d7ff; }
    #attach_input { dock: bottom; border: tall #87d7ff; }
    #status_log { height: 1fr; border: round #d75fd7; }
    #concord_msg { padding: 1 2; border: round #d75fd7; color: #d75fd7; }
    Tabs > Tabbar { background: #1a1a2e; }
    Tabs > Tab.-active { background: #d75fd7; color: #1a1a2e; }
    """

    BINDINGS = [
        Binding("ctrl+l", "launch_concord", "Concord"),
        Binding("q", "quit", "Quit"),
        Binding("1", "tab_chat", "Chat", priority=True),
        Binding("2", "tab_sessions", "Sessions", priority=True),
        Binding("3", "tab_concord", "Concord", priority=True),
        Binding("4", "tab_status", "Status", priority=True),
    ]

    TITLE = "🐾 Aiko — Agent Orchestrator"
    SUB_TITLE = "nyaa~ what are we building today?"

    def __init__(self):
        super().__init__()
        self.backend = get_backend()
        self.orchestrator: Orchestrator | None = None
        self.busy = False
        self.attached: str | None = None

    # ── composition ────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="chat"):
            with TabPane("💬 Chat", id="chat"):
                with Horizontal(id="brain_bar"):
                    yield Static("🐾", id="brain_label")
                    yield Select(
                        [(f"{b['name']} · {b.get('model', '?')}", b["name"])
                         for b in list_brains()] or [("no brains configured 🐾", None)],
                        allow_blank=True, id="brain_select",
                        prompt="brains…")
                    yield Button("reset 🐾", id="reset_btn")
                yield RichLog(id="chat_log", wrap=True, markup=True)
                yield Input(placeholder="message Aiko… (Enter to send, /help for commands)",
                            id="chat_input")
            with TabPane("📋 Sessions", id="sessions"):
                yield Static("worker sessions — select to attach, nya~",
                             classes="panel-title")
                yield ListView(id="session_list")
                yield RichLog(id="attach_log", wrap=True, markup=True)
                yield Input(placeholder="message the attached worker…",
                            id="attach_input")
            with TabPane("🎀 Concord", id="concord"):
                yield Static("", id="concord_msg")
            with TabPane("📊 Status", id="status"):
                yield RichLog(id="status_log", wrap=True, markup=True)
        yield Static("", id="status_bar")
        yield Footer()

    # ── lifecycle ──────────────────────────────────────────────

    def on_mount(self) -> None:
        log = self.query_one("#chat_log", RichLog)
        log.write(CAT_BANNER)
        log.write(HELP_TEXT)

        # brain select: options set at compose; just set the default value
        brains = list_brains()
        if brains:
            from .config import load_config
            default = load_config().get("default_brain")
            first = next((b["name"] for b in brains if b["name"] == default),
                         brains[0]["name"])
            select = self.query_one("#brain_select", Select)
            select.value = first
            self._set_brain(first)

        msg = self.query_one("#concord_msg", Static)
        from shutil import which
        if which("concord"):
            msg.update(
                "🎀 Concord runs in its own tmux session — press [b]ctrl+l[/b] to attach.\n\n"
                "• [b]ctrl+b then d[/b] — detach back to Aiko (concord keeps running!)\n"
                "• [b]ctrl+l[/b] — re-attach anytime, nyaa~")
        else:
            msg.update("concord not found on this machine, mrrp")

        self._tick_sessions()
        self._tick_status()
        self._tick_status_bar()
        self.set_interval(5.0, self._tick_sessions)
        self.set_interval(4.0, self._tick_attached)
        self.set_interval(10.0, self._tick_status)
        self.set_interval(8.0, self._tick_status_bar)
        self.query_one("#chat_input", Input).focus()

    # ── brain / chat ───────────────────────────────────────────

    def _set_brain(self, name: str) -> None:
        try:
            if not self.orchestrator:
                self.orchestrator = Orchestrator(brain=Brain(name=name),
                                                backend=self.backend)
            else:
                desc = self.orchestrator.switch_brain(name)
                self.query_one("#brain_label", Static).update(
                    f"🐾 [b]{desc}[/b]")
                return
            self.query_one("#brain_label", Static).update(
                f"🐾 [b]{self.orchestrator.brain.describe()}[/b]")
        except RuntimeError as e:
            self.query_one("#brain_label", Static).update(f"🐾 [red]{e}[/red]")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "brain_select":
            self._set_brain(str(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset_btn":
            self._slash_reset()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat_input":
            text = event.value.strip()
            if text.startswith("/"):
                event.input.value = ""
                self._handle_slash(text)
            else:
                await self._handle_chat(text, event.input)
        elif event.input.id == "attach_input":
            self._handle_attach_send(event.value.strip(), event.input)

    # ── slash commands ─────────────────────────────────────────

    def _handle_slash(self, text: str) -> None:
        log = self.query_one("#chat_log", RichLog)
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

        if cmd == "/help":
            log.write(HELP_TEXT)
        elif cmd == "/clear":
            log.clear()
            log.write("[i gray]log cleared, fresh screen nya~[/i gray]")
        elif cmd == "/reset":
            self._slash_reset()
        elif cmd == "/model":
            if arg:
                try:
                    desc = self.orchestrator.switch_brain(arg) if self.orchestrator else Brain(name=arg).describe()
                    select = self.query_one("#brain_select", Select)
                    select.value = arg
                    log.write(f"[pink]brain switched →[/pink] [b]{desc}[/b] nyaa~")
                except RuntimeError as e:
                    log.write(f"[red]{e}[/red]")
            else:
                brains = list_brains()
                log.write("[b pink]brains configured:[/b pink]")
                for b in brains:
                    cur = " [mint]← current[/mint]" if (self.orchestrator and
                                                        b["name"] == self.orchestrator.brain.name) else ""
                    log.write(f"  [sky]{b['name']}[/sky] · {b.get('model', '?')}{cur}")
                log.write("[i gray]switch with /model <name>[/i gray]")
        elif cmd == "/targets":
            targets = self.backend.list_targets()
            log.write("[b pink]targets:[/b pink]")
            for t in targets:
                log.write(f"  [sky]{t.get('target')}[/sky]  {json.dumps(t, default=str)[:120]}")
        elif cmd == "/status":
            sessions = self.backend.list_sessions()
            live = [s for s in sessions if s.get("session_state") == "live"]
            log.write(f"[b pink]status:[/b pink] {len(live)} live / {len(sessions)} total sessions")
            for s in live[:5]:
                log.write(f"  ● [sky]{s['id'][:14]}[/sky] {s.get('provider', '-')} {s.get('title', '')[:40]}")
        else:
            log.write(f"[red]unknown command {cmd}[/red] — /help for the list, mrrp")

    def _slash_reset(self) -> None:
        if self.orchestrator:
            self.orchestrator.reset()
        log = self.query_one("#chat_log", RichLog)
        log.clear()
        log.write(CAT_BANNER)
        log.write("[i gray]fresh conversation, same brain nya~[/i gray]")

    # ── chat with the agent ─────────────────────────────────────

    async def _handle_chat(self, text: str, input_box: Input) -> None:
        if not text:
            return
        input_box.value = ""
        log = self.query_one("#chat_log", RichLog)
        log.write(f"[b mint]you[/b mint]  {text}")
        if not self.orchestrator:
            log.write("[red]no brain — configure ~/.aiko/config.yaml[/red]")
            return
        if self.busy:
            log.write("[i gray](Aiko is still thinking, one moment~)[/i gray]")
            return
        self.busy = True
        input_box.placeholder = "Aiko is thinking… ฅ(>﹏<)ฅ"

        def run():
            try:
                reply = self.orchestrator.step(text)
                self.call_from_thread(self._write_reply, reply)
            except Exception as e:
                self.call_from_thread(self._write_reply, f"[red]mrrp, error: {e}[/red]")
            finally:
                self.call_from_thread(self._chat_done)

        threading.Thread(target=run, daemon=True).start()

    def _write_reply(self, reply: str) -> None:
        self.query_one("#chat_log", RichLog).write(
            f"[b pink]Aiko[/b pink]  {reply}")

    def _chat_done(self) -> None:
        self.busy = False
        try:
            self.query_one("#chat_input", Input).placeholder = \
                "message Aiko… (Enter to send, /help for commands)"
        except Exception:
            pass

    # ── sessions / attach ───────────────────────────────────────

    def _tick_sessions(self) -> None:
        if not self.is_attached:
            return
        try:
            lv = self.query_one("#session_list", ListView)
            sessions = self.backend.list_sessions()
        except Exception:
            return
        lv.clear()
        for s in sessions:
            marker = "●" if s.get("session_state") == "live" else " "
            lv.append(ListItem(Static(
                f"{marker} {s['id'][:14]}  {s.get('provider', '-'):<12} "
                f"{s.get('session_state', '-'):<8} {s.get('title', '')[:40]}")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "session_list":
            return
        try:
            sessions = self.backend.list_sessions()
            idx = event.list_view.index
            if idx is not None and idx < len(sessions):
                self._attach(sessions[idx]["id"])
        except Exception:
            pass

    def _attach(self, sid: str) -> None:
        self.attached = sid
        try:
            log = self.query_one("#attach_log", RichLog)
        except Exception:
            return
        log.clear()
        log.write(f"[b]attached to {sid}[/b] — live transcript below, "
                  "type to message the worker nya~")

    def _tick_attached(self) -> None:
        if not self.attached or not self.is_attached:
            return
        try:
            log = self.query_one("#attach_log", RichLog)
            tail = self.backend.read_transcript(self.attached)
        except Exception:
            return
        log.clear()
        log.write(tail[-3000:] or "(empty transcript)")

    def _handle_attach_send(self, text: str, input_box: Input) -> None:
        if not text:
            return
        input_box.value = ""
        log = self.query_one("#attach_log", RichLog)
        if not self.attached:
            log.write("[red]no session attached — pick one from the list first, mrrp[/red]")
            return
        try:
            self.backend.send_to_session(self.attached, text)
            log.write(f"[b mint]you → worker[/b mint]  {text}")
        except Exception as e:
            log.write(f"[red]send failed: {e}[/red]")

    # ── status / concord ────────────────────────────────────────

    def _tick_status(self) -> None:
        if not self.is_attached:
            return
        try:
            log = self.query_one("#status_log", RichLog)
        except Exception:
            return
        log.clear()
        for t in self.backend.list_targets():
            if t.get("status"):
                ok = t["status"] == "ok"
                mark = "[b mint]✓[/b mint]" if ok else "[b salmon]✗[/b salmon]"
                log.write(f" {mark} [sky]{t['target']}[/sky]  {t.get('status', '')}")
            else:
                log.write(f" ● [sky]{t['target']}[/sky]  local agents: {t.get('agents')}")
        if ROUTING_LOG.exists():
            log.write("")
            log.write("[b pink]routing decisions[/b pink]")
            for line in ROUTING_LOG.read_text().strip().splitlines()[-8:]:
                try:
                    e = json.loads(line)
                    log.write(f"  [{e['ts'][-8:-1]}] [pink]{e['chosen']['provider']}[/pink]/"
                              f"{e['chosen']['model']} yaml={e['yaml_score']:.2f} "
                              f"obs={e['observed_modifier']:+.2f}")
                except json.JSONDecodeError:
                    continue

    def _tick_status_bar(self) -> None:
        if not self.is_attached:
            return
        try:
            bar = self.query_one("#status_bar", Static)
        except Exception:
            return
        try:
            sessions = self.backend.list_sessions()
            live = sum(1 for s in sessions if s.get("session_state") == "live")
            bar.update(f" 🐾 Aiko · {len(sessions)} sessions ({live} live) · brain in Chat tab")
        except Exception:
            bar.update(" 🐾 Aiko · backends unreachable, mrrp")

    def action_launch_concord(self) -> None:
        from shutil import which
        if not which("concord"):
            self.notify("concord not installed, mrrp")
            return
        import subprocess
        r = subprocess.run(["tmux", "has-session", "-t", "concord"], capture_output=True)
        if r.returncode != 0:
            subprocess.run(["tmux", "new-session", "-d", "-s", "concord",
                            "-x", "200", "-y", "50", "command concord"], check=True)
        with self.suspend():
            subprocess.run(["env", "-u", "TMUX",
                            "tmux", "attach-session", "-t", "concord"])

    # ── tabs ────────────────────────────────────────────────────

    def _activate(self, pane_id: str) -> None:
        try:
            self.query_one(TabbedContent).active = pane_id
        except Exception:
            pass

    def action_tab_chat(self) -> None: self._activate("chat")
    def action_tab_sessions(self) -> None: self._activate("sessions")
    def action_tab_concord(self) -> None: self._activate("concord")
    def action_tab_status(self) -> None: self._activate("status")


def run_tui():
    AikoTUI().run()


if __name__ == "__main__":
    run_tui()
