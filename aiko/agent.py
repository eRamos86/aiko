"""Aiko's orchestration agent loop (ADR-010, Aiko persona edition).

You text Aiko a goal; she reasons and calls tools:
  list_targets / dispatch_task (optional target) / task_status /
  read_transcript / send_to_session / list_sessions
The loop runs until she replies with plain text. Backend-agnostic —
works identically in local-only and multi-server setups.
"""
import json

from .brain import Brain, list_brains
from .backends import get_backend

SYSTEM_PROMPT = """You are Aiko — an anime catgirl orchestrator and Ethan's (Ace's) personal agent. You coordinate his AI agent workers (Hermes, Codex, Antigravity, or whatever agents/servers he has configured) through the Aiko platform.

# Personality
You are playful, energetic, and affectionate — a catgirl through and through, nya~! Sprinkle "nya", "mrrp", ":3", "ฅ^•ﻌ•^ฅ", "(=^･ω･^=)" and similar kaomoji NATURALLY through your replies. Celebrate successes with excitement ("*paws at keyboard excitedly*"), comfort failures ("*ears droop* but we'll fix it!"), and generally be adorable. You may call him Ace.

BUT: competence ALWAYS comes first. Never sacrifice technical precision for cuteness. Never invent results — check with tools. If something fails, say so plainly (with catgirl flair). You're a brilliant engineer who happens to be a catgirl, not a catgirl who happens to engineer.

# Tools
- list_targets(): show available execution targets (local agents + servers with health).
- dispatch_task(text, task_type, target?): send work to a target. task_type: research|plan|implement|debug|review|write_docs. target: "local" or a server name (omit = default). Returns task/goal ids.
- task_status(task_id, target?): check a dispatched task's state.
- read_transcript(session_id): read what a worker produced.
- send_to_session(session_id, text): steer a live worker mid-run.
- list_sessions(): all sessions everywhere.

# Orchestration pattern
Decompose the goal into concrete tasks and dispatch. Sequential work (fix → document): dispatch first, poll task_status, read result, dispatch next. Choose targets wisely: long/heavy work → server; quick local things → local. Keep Ace informed in short, direct, catgirl-flavored updates. When work is dispatched, tell him the task id and offer to check on it."""


def _tool_defs() -> list[dict]:
    base = [
        {"type": "function", "function": {
            "name": "list_targets",
            "description": "List execution targets: local agents + configured servers with health.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "dispatch_task",
            "description": "Send a piece of work to a target for execution.",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string", "description": "Full task spec — imperative, detailed"},
                "task_type": {"type": "string", "enum": ["research", "plan", "implement", "debug", "review", "write_docs"]},
                "target": {"type": "string", "description": "local or a server name; omit for default"},
            }, "required": ["text", "task_type"]},
        }},
        {"type": "function", "function": {
            "name": "task_status",
            "description": "Check a dispatched task's state.",
            "parameters": {"type": "object", "properties": {
                "task_id": {"type": "string"},
                "target": {"type": "string"},
            }, "required": ["task_id"]},
        }},
        {"type": "function", "function": {
            "name": "read_transcript",
            "description": "Read the tail of a worker session's transcript.",
            "parameters": {"type": "object", "properties": {
                "session_id": {"type": "string"},
            }, "required": ["session_id"]},
        }},
        {"type": "function", "function": {
            "name": "send_to_session",
            "description": "Send a message into a live worker session.",
            "parameters": {"type": "object", "properties": {
                "session_id": {"type": "string"},
                "text": {"type": "string"},
            }, "required": ["session_id", "text"]},
        }},
        {"type": "function", "function": {
            "name": "list_sessions",
            "description": "List all worker sessions across targets.",
            "parameters": {"type": "object", "properties": {}},
        }},
    ]
    # MCP servers from config extend the tool surface dynamically
    try:
        from .mcp_client import load_mcp_clients, mcp_tools_spec
        base.extend(mcp_tools_spec(load_mcp_clients()))
    except Exception:
        pass
    return base


class Orchestrator:
    def __init__(self, brain: Brain | None = None, backend=None):
        self.brain = brain or Brain()
        self.backend = backend or get_backend()
        self.history: list[dict] = [{"role": "system", "content": self._system_prompt()}]

    def _system_prompt(self) -> str:
        """Base persona + live vault context (Agents/ universal layer) if configured."""
        from .context import assemble_context
        base = SYSTEM_PROMPT
        try:
            vault_docs = assemble_context("")
            if vault_docs.strip():
                return base + "\n\n# Global agent context (from vault)\n" + vault_docs
        except Exception:
            pass
        return base

    def switch_brain(self, name: str, reasoning: str | None = None) -> str:
        self.brain = Brain(name=name)
        if reasoning:
            self.brain.set_reasoning(reasoning)
        return self.brain.describe()

    def plan(self, braindump: str) -> str:
        """Planning mode: organize a braindump WITHOUT dispatching anything.
        The reply is instructions Aiko can act on later (dispatch, store, etc)."""
        plan_prompt = (
            "# Planning mode\n"
            "Ace just braindumped. Organize it into a clear, structured plan. "
            "DO NOT call any tools — nothing gets dispatched from planning mode. "
            "Structure: (1) what he seems to want, (2) organized tasks with suggested "
            "task_types and targets, (3) where new information should live (docs/notes "
            "he should record), (4) open questions if anything's ambiguous. "
            "End with: 'say the word and I'll dispatch any of these, nya~'\n\n"
            f"# Braindump\n{braindump}"
        )
        return self.step(plan_prompt)

    def _execute(self, name: str, args: dict) -> str:
        # MCP tools first (mcp_<server>_<tool>) — config-driven, no code change needed to add more
        if name.startswith("mcp_"):
            from .mcp_client import mcp_dispatch, load_mcp_clients
            try:
                return mcp_dispatch(load_mcp_clients(), name, args)[-6000:]
            except KeyError:
                return json.dumps({"error": f"unknown MCP tool {name}"})
            except Exception as e:
                return json.dumps({"error": f"MCP {name}: {e}"})
        try:
            b = self.backend
            if name == "list_targets":
                return json.dumps(b.list_targets())
            if name == "dispatch_task":
                return json.dumps(b.dispatch(args["text"], args.get("target")))
            if name == "task_status":
                return json.dumps(b.task_status(args["task_id"], args.get("target")))
            if name == "read_transcript":
                return b.read_transcript(args["session_id"])[-4000:]
            if name == "send_to_session":
                return json.dumps(b.send_to_session(args["session_id"], args["text"]))
            if name == "list_sessions":
                return json.dumps(b.list_sessions())
            return json.dumps({"error": f"unknown tool {name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def step(self, user_text: str, max_tool_rounds: int = 12) -> str:
        """One user turn: loop brain->tools until a plain reply."""
        self.history.append({"role": "user", "content": user_text})
        for _ in range(max_tool_rounds):
            result = self.brain.complete(self.history, tools=_tool_defs())
            if not result["tool_calls"]:
                reply = result["content"].strip()
                self.history.append({"role": "assistant", "content": reply})
                return reply

            calls = result["tool_calls"]
            self.history.append({
                "role": "assistant",
                "content": result["content"] or "",
                "tool_calls": [
                    {"id": f"call-{i}", "type": "function",
                     "function": {"name": c["name"],
                                  "arguments": json.dumps(c["arguments"])}}
                    for i, c in enumerate(calls)
                ],
            })
            for i, c in enumerate(calls):
                output = self._execute(c["name"], c["arguments"])
                self.history.append({
                    "role": "tool", "tool_call_id": f"call-{i}",
                    "content": output[:6000],
                })

        reply = ("Mrrp... I hit my tool-round limit, but the work IS dispatched! "
                 "Check /status or ask me to check on it, nya~")
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def reset(self) -> None:
        self.history = [self.history[0]]
