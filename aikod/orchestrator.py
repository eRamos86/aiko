"""Server-side orchestrator — delegation stays on the server (ADR-012).

The local Aiko orchestrates everything she dispatches. When work lands on
a server's aikod, that server runs ITS OWN orchestrator loop over the
session: follow-up rounds, reading transcripts, steering the worker, and
replying to the goal — WITHOUT round-tripping to the local client.

Reuses the client's Brain (NIM/Ollama) but with server-local tools:
- dispatch_subtask: create a child task in THIS daemon's DB (stays on server)
- task_status / read_transcript / send_to_session: local DB + tmux ops
- mcp tools: the daemon's own MCP client (vault at 127.0.0.1:4011)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .brain_server import ServerBrain
from .db import connect
from .events import append
from .scheduler import infer_task_type

TRANSCRIPTS = Path.home() / ".aikod" / "transcripts"


class ServerOrchestrator:
    """Orchestrates follow-up work around one routed task, server-side."""

    def __init__(self, db_path, task_id: str, config_path: Path | None = None,
                 brain_factory=None):
        self.db_path = Path(db_path)
        self.task_id = task_id
        self.brain = (brain_factory() if brain_factory
                      else ServerBrain(config_path))
        self.history: list[dict] = [
            {"role": "system", "content": self._system_prompt()}
        ]

    # ── identity + context ─────────────────────────────────────

    def _system_prompt(self) -> str:
        conn = connect(self.db_path)
        task = conn.execute("SELECT title, spec FROM task WHERE id=?",
                             (self.task_id,)).fetchone()
        title, spec = (task or ("?", "?"))
        return (
            "You are Aiko running server-side on Ace's poopmachine daemon. "
            "Your job: complete the delegated task fully on THIS server — "
            "do not ask the user anything, do not wait for the local client. "
            "Use your tools to create subtasks (they stay here), read worker "
            "transcripts, steer live workers, and finish the goal. Reply "
            "concisely in catgirl style when the task completes. "
            f"# Task\n{title}\n\n# Spec\n{spec}"
        )

    # ── tool execution (server-local) ───────────────────────────

    def _execute(self, name: str, args: dict) -> str:
        conn = connect(self.db_path)
        try:
            if name == "dispatch_subtask":
                parent = conn.execute("SELECT goal_id FROM task WHERE id=?",
                                      (self.task_id,)).fetchone()
                goal_id = parent[0] if parent else self.task_id
                sub_id = f"task-{uuid4_hex()}"
                conn.execute(
                    "INSERT INTO task(id, goal_id, parent_task_id, title, spec, "
                    "state, depth, fingerprint) VALUES (?,?,?,?,?, 'ready', ?, ?)",
                    (sub_id, goal_id, self.task_id, args["title"][:80],
                     args["spec"], self._depth(conn) + 1, uuid4_hex()),
                )
                append(conn, sub_id, "task.created",
                       {"title": args["title"], "by": "server-orchestrator",
                        "parent": self.task_id})
                return json.dumps({"task_id": sub_id,
                                   "note": "subtask queued on this server"})
            if name == "task_status":
                row = conn.execute(
                    "SELECT state, assigned_provider, assigned_model FROM task WHERE id=?",
                    (args["task_id"],)).fetchone()
                if not row:
                    return json.dumps({"error": "no such task"})
                return json.dumps({"state": row[0], "provider": row[1],
                                   "model": row[2]})
            if name == "read_transcript":
                return self._read_transcript(args["session_id"])[-4000:]
            if name == "send_to_session":
                return self._send_to_session(args["session_id"], args["text"])
            if name == "list_sessions":
                rows = conn.execute(
                    "SELECT s.id, s.provider_id, s.state, t.title FROM session s "
                    "JOIN task t ON s.task_id = t.id "
                    "WHERE t.goal_id = (SELECT goal_id FROM task WHERE id=?) "
                    "ORDER BY s.id", (self.task_id,)).fetchone() and conn.execute(
                    "SELECT s.id, s.provider_id, s.state, t.title FROM session s "
                    "JOIN task t ON s.task_id = t.id "
                    "WHERE t.goal_id = (SELECT goal_id FROM task WHERE id=?) "
                    "ORDER BY s.id", (self.task_id,)).fetchall() or []
                return json.dumps([{"id": r[0], "provider": r[1],
                                    "state": r[2], "title": r[3]} for r in rows])
            return json.dumps({"error": f"unknown tool {name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _depth(self, conn) -> int:
        row = conn.execute("SELECT depth FROM task WHERE id=?",
                           (self.task_id,)).fetchone()
        return row[0] if row else 0

    def _read_transcript(self, sid: str) -> str:
        for cand in TRANSCRIPTS.glob(f"{sid}*"):
            if cand.is_file():
                return cand.read_text(errors="replace")
        return "(no transcript)"

    def _send_to_session(self, sid: str, text: str) -> str:
        r = subprocess.run(["tmux", "has-session", "-t", f"aiko-{sid}"],
                           capture_output=True)
        if r.returncode != 0:
            return json.dumps({"error": f"session aiko-{sid} not alive"})
        subprocess.run(["tmux", "send-keys", "-t", f"aiko-{sid}", "-l", text],
                       check=True)
        subprocess.run(["tmux", "send-keys", "-t", f"aiko-{sid}", "Enter"],
                       check=True)
        return json.dumps({"sent": True})

    # ── the agent loop (mirrors aiko.agent.Orchestrator.step) ───

    def step(self, user_text: str, max_tool_rounds: int = 12) -> str:
        self.history.append({"role": "user", "content": user_text})
        for _ in range(max_tool_rounds):
            result = self.brain.complete(self.history, tools=_TOOL_DEFS)
            calls = result["tool_calls"]
            if not calls:
                reply = result["content"].strip()
                append_reply = reply
                self.history.append({"role": "assistant", "content": reply})
                return reply
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
                result_text = self._execute(c["name"], c["arguments"])
                self.history.append({"role": "tool", "tool_call_id": f"call-{i}",
                                     "content": result_text})
        return "(hit the tool-round limit, nya…)"


def uuid4_hex() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


_TOOL_DEFS = [
    {"type": "function", "function": {
        "name": "dispatch_subtask",
        "description": "Queue a subtask of the current task on THIS server. "
                       "It stays here — no round-trip to the local client.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "short title"},
            "spec": {"type": "string", "description": "full imperative spec"},
            "task_type": {"type": "string",
                          "enum": ["research", "plan", "implement", "debug",
                                   "review", "write_docs"]},
        }, "required": ["title", "spec"]},
    }},
    {"type": "function", "function": {
        "name": "task_status",
        "description": "Check a task's state on this server.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string"},
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
        "description": "Send a message into a live worker's tmux pane.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string"},
            "text": {"type": "string"},
        }, "required": ["session_id", "text"]},
    }},
    {"type": "function", "function": {
        "name": "list_sessions",
        "description": "List worker sessions in this goal.",
        "parameters": {"type": "object", "properties": {}},
    }},
]
