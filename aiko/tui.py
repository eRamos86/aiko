"""Aiko TUI — chat-centric catgirl orchestrator client.

v1.1: interactive /model picker (arrows + reasoning), sessions split
active/inactive sorted by client→agent, concord opens on tab activation,
/new replaces /reset, /plan for braindumps.
"""
import json
import subprocess
import threading
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (Button, Footer, Header, Input, ListItem, ListView,
                             OptionList, RichLog, Select, Static, TabbedContent,
                             TabPane, Tabs)

from .agent import Orchestrator
from .brain import Brain, list_brains
from .backends import get_backend
from .boot import BootScreen

ROUTING_LOG = Path.home() / ".aiko" / "routing.log"

CAT_BANNER = r"""
  /\_/\   Aiko — Agent Orchestrator
 ( •ᴗ• )  for all your AI Agents, nya~
 / >💻    type a goal below! ฅ^•ﻌ•^ฅ
"""

HELP_TEXT = """[b pink]Aiko slash commands[/b pink]
  [sky]/model[/sky]            pick brain (↑↓ model, ←→ reasoning, Enter confirm)
  [sky]/plan[/sky] [i]<dump>[/i]      braindump → organized plan (nothing dispatched)
  [sky]/targets[/sky]          list execution targets (local + servers)
  [sky]/usage[/sky]            usage economy: budgets, consumption, cooldowns
  [sky]/status[/sky]           quick session + target overview
  [sky]/new[/sky]              fresh conversation (same brain)
  [sky]/clear[/sky]            clear the chat log
  [sky]/help[/sky]             this message

[i gray]keys: 1 chat · 2 sessions · 3 concord · 4 status · v pager(copy) · q quit[/i gray]"""

REASONING_LEVELS = ["low", "medium", "high"]


# ── interactive model picker ─────────────────────────────────────

class ModelPickerScreen(ModalScreen):
    """↑↓ pick brain · ←→ cycle reasoning · Enter confirm · Esc cancel."""
    CSS = """
    #picker { width: 64; height: auto; border: thick #d75fd7; background: #1a1a2e; padding: 1; }
    #picker_title { color: #ff87ff; text-style: bold; padding: 0 1; }
    #model_list { height: auto; max-height: 12; }
    #reasoning_row { height: 1; padding: 0 1; color: #87d7ff; }
    """
    BINDINGS = [
        Binding("left", "reason_left", "reasoning −"),
        Binding("right", "reason_right", "reasoning +"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, current_brain: str | None, current_reasoning: str):
        super().__init__()
        self.current_brain = current_brain
        self.reasoning_index = max(0, REASONING_LEVELS.index(current_reasoning)
                                  if current_reasoning in REASONING_LEVELS else 1)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("🐾  pick a brain, nya~", id="picker_title")
            ol = OptionList(id="model_list")
            brains = list_brains()
            for b in brains:
                label = f"{b['name']} · {b.get('model', '?')}"
                ol.add_option(label)
                if b["name"] == self.current_brain:
                    ol.highlighted = len(ol.options) - 1
            yield ol
            yield Static("", id="reasoning_row")

    def on_mount(self) -> None:
        self._update_reasoning_row()

    def _update_reasoning_row(self) -> None:
        level = REASONING_LEVELS[self.reasoning_index]
        self.query_one("#reasoning_row", Static).update(
            f"  reasoning:  ← ‹ {level} › →")

    def action_reason_left(self) -> None:
        self.reasoning_index = max(0, self.reasoning_index - 1)
        self._update_reasoning_row()

    def action_reason_right(self) -> None:
        self.reasoning_index = min(len(REASONING_LEVELS) - 1, self.reasoning_index + 1)
        self._update_reasoning_row()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event) -> None:
        brains = list_brains()
        idx = event.option_index if hasattr(event, "option_index") else None
        # Textual OptionList event: OptionList.OptionSelected has option_id or index
        try:
            index = event.option_index
        except AttributeError:
            ol = self.query_one("#model_list", OptionList)
            index = ol.highlighted
        if index is not None and 0 <= index < len(brains):
            self.dismiss({
                "brain": brains[index]["name"],
                "reasoning": REASONING_LEVELS[self.reasoning_index],
            })
        else:
            self.dismiss(None)


# ── main app ─────────────────────────────────────────────────────

class AikoTUI(App):
    CSS = """
    Screen { background: #0f0f1a; }
    Header { background: #1a1a2e; color: #ffafff; border-bottom: solid #d75fd7; }
    Footer { background: #1a1a2e; color: #87d7ff; }
    #status_bar { dock: bottom; height: 1; background: #1a1a2e; color: #ffafff; }
    #chat_log { height: 1fr; border: round #d75fd7; border-title-color: #ffafff;
                padding: 0 1; }
    #chat_input { dock: bottom; border: tall #d75fd7; background: #14142a; }
    #chat_input:focus { border: tall #ff87ff; }
    #stream_view {
        height: auto; max-height: 14; border-left: thick #d75fd7;
        padding: 0 1; margin: 0 1; display: none; color: #87d7ff;
        background: #14142a;
    }
    #stream_view.on { display: block; }
    #brain_bar { dock: top; height: 3; background: #14142a; }
    #brain_label { color: #d75fd7; width: auto; padding: 0 1; }
    #brain_select { width: 1fr; border: none; background: #14142a; }
    #brain_select:hover { border: none; }
    #reset_btn { margin-left: 1; background: #1a1a2e; color: #ffafff;
                 border: round #d75fd7; }
    #session_bar { dock: top; height: 2; background: #14142a; }
    #sessions_hint { color: #6c6c8a; padding: 0 1; }
    #refresh_btn { background: #1a1a2e; color: #ffafff;
                   border: round #d75fd7; margin: 0 1; }
    #session_list { height: 40%; border: round #d75fd7; background: #12121f; }
    #session_list > ListItem { padding: 0 1; }
    #session_list > ListItem:hover { background: #1e1e36; }
    #session_list > ListItem.--highlight { background: #2a1e3e; }
    #story_header { height: auto; max-height: 12; border-left: thick #87d7ff;
                    padding: 0 1; margin: 0 1; background: #14142a;
                    color: #e8e6f8; }
    #story_tabs Tabs.Tab { padding: 0 2; }
    #story_tabs Tab.-active { background: #d75fd7; color: #0f0f1a; }
    #attach_log { height: 1fr; border: round #87d7ff; background: #12121f;
                  padding: 0 1; }
    #attach_input { dock: bottom; border: tall #87d7ff; background: #14142a; }
    #status_log { height: 1fr; border: round #d75fd7; background: #12121f; }
    #concord_msg { padding: 1 2; border: round #d75fd7; color: #d75fd7; }
    Tabs { background: #1a1a2e; }
    Tabs > Tab { padding: 0 2; color: #6c6c8a; }
    Tabs > Tab.-active { color: #ffafff; }
    TabbedContent > Tabs { dock: top; }
    TabbedContent > ContentPages { height: 1fr; }
    """

    BINDINGS = [
        Binding("ctrl+l", "launch_concord", "Concord"),
        Binding("ctrl+1", "tab_chat", "Chat", priority=True),
        Binding("ctrl+2", "tab_sessions", "Sessions", priority=True),
        Binding("ctrl+3", "tab_concord", "Concord", priority=True),
        Binding("ctrl+4", "tab_status", "Status", priority=True),
        Binding("ctrl+v", "pager", "Pager", priority=True),
        Binding("v", "pager", "Pager/copy", priority=True),
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
        self._concord_open = False
        self._story_sessions: list[dict] = []
        self._story_active_tab: int = 0
        self._transcript_len: dict[str, int] = {}
        self._transcript_head: dict[str, str] = {}

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
                        allow_blank=True, id="brain_select", prompt="brains…")
                    yield Button("reset 🐾", id="reset_btn")
                yield RichLog(id="chat_log", wrap=True, markup=True)
                yield Static("", id="stream_view")
                yield Input(placeholder="message Aiko… (Enter to send, /help for commands)",
                            id="chat_input")
            with TabPane("📋 Sessions", id="sessions"):
                with Horizontal(id="session_bar"):
                    yield Static("● live ○ done  ·  select a session to see its story, nya~",
                                 id="sessions_hint")
                    yield Button("refresh 🐾", id="refresh_btn")
                yield ListView(id="session_list")
                yield Static("", id="story_header")
                yield Tabs(id="story_tabs")
                yield RichLog(id="attach_log", wrap=True, markup=True)
                yield Input(placeholder="message the attached worker… (v = pager for copy)",
                            id="attach_input")
            with TabPane("🎀 Concord", id="concord"):
                yield Static("", id="concord_msg")
            with TabPane("📊 Status", id="status"):
                yield RichLog(id="status_log", wrap=True, markup=True)
        yield Static("", id="status_bar")
        yield Footer()

    # ── lifecycle ──────────────────────────────────────────────

    def on_mount(self) -> None:
        # gather real boot facts in a thread while the animation plays
        facts: list[tuple[str, str]] = []
        boot = BootScreen(step_results=facts)

        def gather():
            try:
                brains = list_brains()
                facts.append(("brains", f"{len(brains)} configured "
                              f"({', '.join(b['name'] for b in brains[:3])}"
                              f"{'…' if len(brains) > 3 else ''})"))
            except Exception as e:
                facts.append(("brains", f"error: {e}"[:60]))
            try:
                targets = self.backend.list_targets()
                servers = [t['target'] for t in targets if t.get('status')]
                locals_ = [t for t in targets if t.get('agents')]
                if servers:
                    facts.append(("servers", f"{len(servers)} reachable "
                                  f"({', '.join(servers)})"))
                if locals_:
                    agents = locals_[0].get('agents', [])
                    facts.append(("local agents", f"{len(agents)} found "
                                  f"({', '.join(agents[:4])})"))
            except Exception:
                facts.append(("targets", "none configured"))
            from shutil import which
            facts.append(("concord", "installed 🐾" if which("concord")
                          else "not found"))

        threading.Thread(target=gather, daemon=True).start()
        self.push_screen(boot)
        self.set_timer(2.4, self._finish_mount)

    def _finish_mount(self) -> None:
        # collect real boot facts (shown next time / instant if re-opened)
        log = self.query_one("#chat_log", RichLog)
        log.write(CAT_BANNER)
        log.write(HELP_TEXT)

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
                "🎀 Concord — activating this tab opens concord fullscreen.\n\n"
                "• come back with [b]ctrl+b then d[/b] (concord keeps running in tmux!)\n"
                "• re-enter the tab (or ctrl+l) anytime to jump back in, nyaa~")
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

    # ── tab activation → concord ────────────────────────────────

    def on_tabbed_content_tab_activated(self, event) -> None:
        pane_id = getattr(event, "pane", None)
        pane_id = getattr(pane_id, "id", pane_id)
        if pane_id == "concord" and not self._concord_open:
            self.action_launch_concord()

    # ── brain / chat ───────────────────────────────────────────

    def _set_brain(self, name: str) -> None:
        try:
            if not self.orchestrator:
                self.orchestrator = Orchestrator(brain=Brain(name=name),
                                                backend=self.backend)
            else:
                self.orchestrator.switch_brain(name)
            self._update_brain_label()
        except RuntimeError as e:
            self.query_one("#brain_label", Static).update(f"🐾 [red]{e}[/red]")

    def _update_brain_label(self) -> None:
        if self.orchestrator:
            b = self.orchestrator.brain
            reason = getattr(b, "reasoning", "medium")
            self.query_one("#brain_label", Static).update(
                f"🐾 [b]{b.describe()}[/b] [gray]· reasoning: {reason}[/gray]")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "brain_select":
            self._set_brain(str(event.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset_btn":
            self._slash_new()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat_input":
            text = event.value.strip()
            if text.startswith("/"):
                event.input.value = ""
                self.run_worker(self._handle_slash(text), exclusive=False)
            else:
                await self._handle_chat(text, event.input)
        elif event.input.id == "attach_input":
            self._handle_attach_send(event.value.strip(), event.input)

    # ── slash commands ─────────────────────────────────────────

    async def _handle_slash(self, text: str) -> None:
        log = self.query_one("#chat_log", RichLog)
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")

        if cmd == "/help":
            log.write(HELP_TEXT)
        elif cmd == "/clear":
            log.clear()
            log.write("[i gray]log cleared, fresh screen nya~[/i gray]")
        elif cmd in ("/new", "/reset"):
            self._slash_new()
        elif cmd == "/model":
            current = self.orchestrator.brain.name if self.orchestrator else None
            current_reason = getattr(self.orchestrator.brain, "reasoning", "medium") if self.orchestrator else "medium"
            picker = ModelPickerScreen(current, current_reason)
            result = await self.push_screen_wait(picker)
            if result:
                self.orchestrator.switch_brain(result["brain"],
                                               reasoning=result["reasoning"])
                select = self.query_one("#brain_select", Select)
                select.value = result["brain"]
                self._update_brain_label()
                log.write(f"[pink]brain switched →[/pink] "
                          f"[b]{self.orchestrator.brain.describe()}[/b] "
                          f"[gray]· reasoning: {result['reasoning']}[/gray] nyaa~")
        elif cmd == "/plan":
            if not arg:
                log.write("[red]/plan needs your braindump, nya — "
                          "/plan <everything on your mind>[/red]")
                return
            log.write("[pink]planning mode[/pink] — organizing your braindump…")
            await self._handle_plan(arg)
        elif cmd == "/targets":
            targets = self.backend.list_targets()
            log.write("[b pink]targets:[/b pink]")
            for t in targets:
                log.write(f"  [sky]{t.get('target')}[/sky]  {json.dumps(t, default=str)[:120]}")
        elif cmd == "/usage":
            from .usage import snapshot_all
            snap = snapshot_all()
            log.write("[b pink]🪙 usage economy[/b pink]")
            if not snap:
                log.write("  [dim](no usage recorded yet, nya)[/dim]")
            for key, info in snap.items():
                cds = info["cooldown_s"]
                state = ("[b red]COOLDOWN " if cds else "")
                log.write(f"  [b sky]{key}[/b sky] {state}"
                          + (f"{cds}s[/b red]" if cds else ""))
                for b in info["budgets"]:
                    log.write(f"    {b['unit']}: {b['used']}/{b['cap']} "
                              f"[dim](resets in {int(b['resets_in'])}s)[/dim]")
                if info["raw_24h"]:
                    raw = " · ".join(f"{u}:{n}" for u, n in sorted(info["raw_24h"].items()))
                    log.write(f"    [dim]24h raw: {raw}[/dim]")
            log.write("[dim]set caps: aiko usage-set <key> <unit> <cap> <window>[/dim]")
        elif cmd == "/status":
            sessions = self.backend.list_sessions()
            live = [s for s in sessions if s.get("session_state") == "live"]
            log.write(f"[b pink]status:[/b pink] {len(live)} live / {len(sessions)} total sessions")
            for s in live[:5]:
                log.write(f"  ● [sky]{s['id'][:14]}[/sky] {s.get('provider', '-')} {s.get('title', '')[:40]}")
        else:
            log.write(f"[red]unknown command {cmd}[/red] — /help for the list, mrrp")

    def _slash_new(self) -> None:
        if self.orchestrator:
            self.orchestrator.reset()
        log = self.query_one("#chat_log", RichLog)
        log.clear()
        log.write(CAT_BANNER)
        log.write("[i gray]fresh conversation, same brain nya~[/i gray]")

    # ── planning mode (/plan) ──────────────────────────────────

    async def _handle_plan(self, braindump: str) -> None:
        log = self.query_one("#chat_log", RichLog)
        log.write(f"[b mint]you (braindump)[/b mint]  {braindump}")
        if not self.orchestrator:
            log.write("[red]no brain — configure ~/.aiko/config.yaml[/red]")
            return
        if self.busy:
            log.write("[i gray](Aiko is still thinking, one moment~)[/i gray]")
            return
        self.busy = True
        self._run_stream(braindump, plan=True)

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
        self._run_stream(text)

    # ── streaming runner: paints thinking / tools / text live ───

    def _run_stream(self, text: str, plan: bool = False) -> None:
        """Iterate the orchestrator's event stream in a worker thread."""
        input_box = self.query_one("#chat_input", Input)
        input_box.placeholder = ("Aiko is planning… ฅ(˘ω˘ )ฅ" if plan
                                 else "Aiko is thinking… ฅ(>﹏<)ฅ")
        self._stream_think = ""
        self._stream_text = ""
        self._stream_tools: list[str] = []

        orch = self.orchestrator
        if orch is None:
            return

        def run():
            try:
                gen = (orch.plan_stream(text) if plan
                       else orch.step_stream(text))
                for evt in gen:
                    self.call_from_thread(self._on_stream_event, evt)
            except Exception as e:
                self.call_from_thread(self._write_reply, f"[red]mrrp, error: {e}[/red]")
            finally:
                self.call_from_thread(self._chat_done)

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _esc(text: str) -> str:
        return text.replace("[", "\\[")

    def _on_stream_event(self, evt: dict) -> None:
        try:
            sv = self.query_one("#stream_view", Static)
        except Exception:
            return
        kind = evt.get("type")
        if kind == "thinking":
            self._stream_think += evt.get("delta", "")
        elif kind == "text":
            self._stream_text += evt.get("delta", "")
        elif kind == "tool":
            self._stream_tools.append(evt.get("name", "?"))
        elif kind == "tool_result":
            if self._stream_tools:
                self._stream_tools[-1] += " ✓"
        elif kind == "reply":
            self.query_one("#chat_log", RichLog).write(
                f"[b pink]Aiko[/b pink]  {evt.get('content', '')}")
            self._stream_think = self._stream_text = ""
            self._stream_tools = []
            sv.remove_class("on")
            sv.update("")
            return
        lines = []
        if self._stream_think:
            lines.append(f"[i dim]💭 {self._esc(self._stream_think[-280:])}[/i dim]")
        for t in self._stream_tools:
            lines.append(f"[b blue]🔧 {self._esc(t)}[/b blue]")
        if self._stream_text:
            lines.append(f"[b pink]Aiko[/b pink]  {self._esc(self._stream_text)}")
        if lines:
            sv.add_class("on")
            sv.update("\n".join(lines))

    def _write_reply(self, reply: str) -> None:
        self.query_one("#chat_log", RichLog).write(
            f"[b pink]Aiko[/b pink]  {reply}")

    def _chat_done(self) -> None:
        self.busy = False
        try:
            self.query_one("#chat_input", Input).placeholder = \
                "message Aiko… (Enter to send, /help for commands)"
            sv = self.query_one("#stream_view", Static)
            sv.remove_class("on")
            sv.update("")
        except Exception:
            pass

    # ── sessions: active/inactive views, sorted client→agent ──

    def _normalize_sessions(self) -> list[dict]:
        rows = []
        for s in self.backend.list_sessions():
            sid = s["id"]
            client = s.get("server") or ("local" if sid.startswith("local-") else "?")
            rows.append({
                "id": sid, "client": client,
                "agent": s.get("provider", "-"),
                "state": s.get("session_state", "?"),
                "title": s.get("title", ""),
            })
        return rows

    def _tick_sessions(self) -> None:
        if not self.is_attached:
            return
        try:
            lv = self.query_one("#session_list", ListView)
        except Exception:
            return
        rows = self._normalize_sessions()
        active = sorted([r for r in rows if r["state"] == "live"],
                         key=lambda r: (r["client"], r["agent"], r["id"]))
        inactive = sorted([r for r in rows if r["state"] != "live"],
                          key=lambda r: (r["client"], r["agent"], r["id"]))
        lv.clear()
        lv.append(ListItem(Static(f"[b pink]── ● active ({len(active)}) ──[/b pink]")))
        for r in active:
            lv.append(ListItem(Static(
                f"  ● [sky]{r['id'][:14]}[/sky]  {r['client']:<12} {r['agent']:<12} {r['title'][:38]}")))
        lv.append(ListItem(Static(f"[b gray]── ○ inactive ({len(inactive)}) ──[/b gray]")))
        for r in inactive:
            lv.append(ListItem(Static(
                f"    [gray]{r['id'][:14]}  {r['client']:<12} {r['agent']:<12} {r['title'][:38]}[/gray]")))
        self._session_rows = active + inactive

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "session_list":
            return
        # section headers occupy indices; map selection to nearest session row
        rows = getattr(self, "_session_rows", [])
        idx = event.list_view.index
        if idx is None or not rows:
            return
        # list layout: 1 header + active rows + 1 header + inactive rows
        n_active = sum(1 for r in rows if r["state"] == "live")
        data_idx = None
        if idx <= n_active and idx >= 1:
            data_idx = idx - 1
        elif idx >= n_active + 2:
            data_idx = n_active + (idx - n_active - 2)
        if data_idx is not None and 0 <= data_idx < len(rows):
            self._attach_session(rows[data_idx]["id"], rows[data_idx]["state"])

    def _attach_session(self, sid: str, state: str = "?") -> None:
        """Attach: load the STORY (goal → decisions → sessions), then transcripts."""
        self.attached = sid
        self._story_data = None
        try:
            hdr = self.query_one("#story_header", Static)
            log = self.query_one("#attach_log", RichLog)
        except Exception:
            return
        log.clear()
        hdr.update(f"[b pink]🐾 loading story for {sid[:14]}…[/b pink]")

        def run():
            try:
                story = self.backend.session_story(sid)
                self.call_from_thread(self._render_story, sid, state, story)
            except Exception as e:
                self.call_from_thread(
                    lambda: hdr.update(f"[red]story failed: {e}[/red]"))

        threading.Thread(target=run, daemon=True).start()

    def _render_story(self, sid: str, state: str, story: dict) -> None:
        """Paint the delegation story: goal → decisions → parallel sessions."""
        self._story_data = story
        try:
            hdr = self.query_one("#story_header", Static)
            tabs = self.query_one("#story_tabs", Tabs)
            log = self.query_one("#attach_log", RichLog)
        except Exception:
            return
        log.clear()
        goal = story.get("goal", {})
        lines = [
            f"[b pink]🐾 goal[/b pink] [sky]{goal.get('id', '?')}[/sky] "
            f"[dim]by {goal.get('by', '?')} · {goal.get('status', '?')}[/dim]",
            f"    {self._esc(goal.get('text', ''))[:120]}",
            "",
        ]
        tasks = story.get("tasks", [])
        decisions = story.get("decisions", {})
        for t in tasks:
            tid = t["id"]
            prov = t.get("provider") or "?"
            model = t.get("model") or "?"
            dec = decisions.get(tid, {})
            reason = dec.get("reasoning") or ""
            mark = "●" if t.get("state") == "running" else (
                "✓" if t.get("state") == "completed" else "○")
            style = "pink" if t.get("state") == "running" else "dim"
            lines.append(
                f"  [{style}]{mark} {t['title'][:60]}[/{style}] "
                f"[sky]{prov}/{model}[/sky]")
            if dec:
                lines.append(f"      [dim]↳ routed to [sky]{dec['provider']}/{dec['model']}[/sky]"
                             f" — {self._esc(reason)[:100]}[/dim]")
        orch = story.get("orchestrator_replies", {})
        if orch:
            lines.append("")
            lines.append("[b mint]🧠 server orchestrator[/b mint]")
            for tid, reply in list(orch.items())[:3]:
                lines.append(f"  [dim]{tid[:14]}:[/dim] {self._esc(reply)[:140]}")
        hdr.update("\n".join(lines))

        # tabs: one per session in this goal (parallel workers)
        tabs.clear()
        sessions = story.get("sessions", [])
        if not sessions:
            tabs.add_tab("no sessions, mrrp")
            return
        for s in sessions:
            mark = "●" if s.get("state") == "live" else "○"
            tabs.add_tab(f"{mark} {s.get('provider', '?')}/{(s.get('model') or '?')[:18]}")
        self._story_sessions = sessions
        # load the first (or the clicked) session's transcript
        target = next((s for s in sessions if s["id"] == sid), sessions[0])
        self._load_session_transcript(target["id"], live=state == "live")

    def _load_session_transcript(self, sid: str, live: bool = False) -> None:
        """Fetch FULL transcript (tail=0) and render without scroll snap."""
        def run():
            try:
                text = self.backend.read_transcript(sid)
                self.call_from_thread(self._write_transcript, sid, text)
            except Exception as e:
                self.call_from_thread(
                    lambda: self.query_one("#attach_log", RichLog).write(
                        f"[red]transcript failed: {e}[/red]"))

        threading.Thread(target=run, daemon=True).start()

    def _write_transcript(self, sid: str, text: str) -> None:
        """Render transcript; live sessions get delta-append (no clear+snap)."""
        try:
            log = self.query_one("#attach_log", RichLog)
        except Exception:
            return
        prev_len = self._transcript_len.get(sid, 0)
        if prev_len and text.startswith(self._transcript_head.get(sid, "\x00")):
            # delta: append only the new part — preserves scroll position
            new_part = text[prev_len:]
            if new_part:
                log.write(self._esc(new_part))
        else:
            log.clear()
            log.write(self._esc(text) or "(empty transcript)")
        self._transcript_len[sid] = len(text)
        self._transcript_head[sid] = text[:200]

    def _tick_attached(self) -> None:
        if not self.attached or not self.is_attached:
            return
        sess = getattr(self, "_story_sessions", None)
        if not sess:
            return
        active_idx = getattr(self, "_story_active_tab", 0)
        if active_idx >= len(sess):
            return
        sid = sess[active_idx]["id"]
        live = sess[active_idx].get("state") == "live"
        if not live:
            return  # finished sessions: no polling, no scroll snap, nya
        self._load_session_transcript(sid, live=True)

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
        self._concord_open = True
        try:
            import subprocess
            r = subprocess.run(["tmux", "has-session", "-t", "concord"],
                               capture_output=True)
            if r.returncode != 0:
                subprocess.run(["tmux", "new-session", "-d", "-s", "concord",
                                "-x", "200", "-y", "50", "command concord"], check=True)
            with self.suspend():
                subprocess.run(["env", "-u", "TMUX",
                                "tmux", "attach-session", "-t", "concord"])
        finally:
            self._concord_open = False

    def on_tabs_tab_activated(self, event) -> None:
        """Story tab switched → load that parallel session's transcript."""
        if not getattr(self, "_story_sessions", None):
            return
        try:
            idx_raw = event.tab_index if hasattr(event, "tab_index") else \
                self.query_one("#story_tabs", Tabs).active
            idx = int(idx_raw) if idx_raw is not None else None
        except Exception:
            return
        if idx is None or not (0 <= idx < len(self._story_sessions)):
            return
        self._story_active_tab = idx
        sid = self._story_sessions[idx]["id"]
        # clear per-session transcript cache for clean render
        self._transcript_len.pop(sid, None)
        self._transcript_head.pop(sid, None)
        try:
            self.query_one("#attach_log", RichLog).clear()
        except Exception:
            pass
        self._load_session_transcript(sid)

    def on_key(self, event) -> None:
        """Global key hook: digits switch tabs even while an Input is focused.

        Inputs consume printable keys before app bindings see them, so we
        intercept here — but ONLY when the input is empty (typing a real
        message like '2fa code' must not jump tabs, nya).
        """
        focused = self.focused
        key = getattr(event, "key", "")
        tab_map = {"1": "chat", "2": "sessions", "3": "concord", "4": "status"}
        if key in tab_map and isinstance(focused, Input):
            if not focused.value:
                event.stop()
                event.prevent_default()
                self._activate(tab_map[key])
        elif key == "v" and isinstance(focused, Input) and not focused.value:
            event.stop()
            event.prevent_default()
            self.action_pager()

    def action_pager(self) -> None:
        """Open the current log in the terminal's native pager (less).

        This is the copy path: mouse events belong to the TUI, but inside
        `less` the terminal regains native mouse selection. q returns.
        """
        from textual.widgets import RichLog as _RL
        pane = self.query("TabbedContent TabPane")
        active_id = self.query_one(TabbedContent).active
        target = "#chat_log" if active_id == "chat" else (
            "#attach_log" if active_id == "sessions" else
            "#status_log" if active_id == "status" else None)
        if not target:
            self.notify("no log here to page, mrrp")
            return
        try:
            log = self.query_one(target, _RL)
            text = "\n".join(
                seg.text for line in log.lines for seg in line)
            text = self._strip_markup(text)
        except Exception as e:
            self.notify(f"pager failed: {e}")
            return
        if not text.strip():
            self.notify("nothing to page, nya")
            return
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(text)
            path = f.name
        with self.suspend():
            subprocess.run(["less", "-R", path])
        import os
        os.unlink(path)

    @staticmethod
    def _strip_markup(text: str) -> str:
        """Remove Textual/Rich markup tags from extracted log text."""
        import re as _re
        return _re.sub(r"\[/?[a-z0-9 #]+\]", "", text)

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
