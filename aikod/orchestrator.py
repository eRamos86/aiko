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
import uuid
from pathlib import Path

from .brain_server import ServerBrain
from .db import connect, database, transaction
from .control import paused
from .events import append
from .scheduler import infer_task_type

TRANSCRIPTS = Path.home() / ".aikod" / "transcripts"


class Deferred(Exception):
    """An update barrier interrupted a resumable orchestration pass."""


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
        with database(self.db_path) as conn:
            row = conn.execute("SELECT history FROM orchestrator_job WHERE task_id=?", (task_id,)).fetchone()
            if row and row[0]:
                self.history = json.loads(row[0])

    def _checkpoint(self):
        with database(self.db_path) as conn:
            conn.execute("INSERT INTO orchestrator_job(task_id,history) VALUES (?,?) "
                         "ON CONFLICT(task_id) DO UPDATE SET history=excluded.history",
                         (self.task_id, json.dumps(self.history)))

    def _check_pause(self):
        with database(self.db_path) as conn:
            if paused(conn):
                raise Deferred()

    # ── identity + context ─────────────────────────────────────

    def _system_prompt(self) -> str:
        with database(self.db_path) as conn:
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

    def _execute(self, name: str, args: dict, call_id=None) -> str:
        conn = connect(self.db_path)
        try:
            if name == "dispatch_subtask":
                with transaction(conn):
                    call_id = call_id or uuid.uuid4().hex
                    effect = conn.execute("SELECT result FROM orchestrator_effect WHERE task_id=? AND call_id=?",
                                          (self.task_id, call_id)).fetchone()
                    if effect:
                        return effect[0]
                    parent = conn.execute("SELECT goal_id FROM task WHERE id=?", (self.task_id,)).fetchone()
                    sub_id = f"task-{uuid4_hex()}"
                    conn.execute(
                        "INSERT INTO task(id,goal_id,parent_task_id,title,spec,state,depth,fingerprint) "
                        "VALUES (?,?,?,?,?,'ready',?,?)",
                        (sub_id, parent[0], self.task_id, args["title"][:80], args["spec"],
                         self._depth(conn) + 1, uuid4_hex()))
                    result = json.dumps({"task_id": sub_id, "note": "subtask queued on this server"})
                    conn.execute("INSERT INTO orchestrator_effect VALUES (?,?,?)", (self.task_id, call_id, result))
                    append(conn, sub_id, "task.created", {"title": args["title"], "by": "server-orchestrator",
                                                         "parent": self.task_id})
                    return result
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
                # Sending keystrokes cannot be atomic with SQLite. Record intent
                # first and never replay an uncertain delivery after a crash.
                call_id = call_id or uuid.uuid4().hex
                with transaction(conn):
                    effect = conn.execute("SELECT result FROM orchestrator_effect WHERE task_id=? AND call_id=?",
                                          (self.task_id, call_id)).fetchone()
                    if effect:
                        return effect[0]
                    conn.execute("INSERT INTO orchestrator_effect VALUES (?,?,?)", (self.task_id, call_id,
                                 json.dumps({"error": "input delivery uncertain; not replayed"})))
                result = self._send_to_session(args["session_id"], args["text"])
                conn.execute("UPDATE orchestrator_effect SET result=? WHERE task_id=? AND call_id=?",
                             (result, self.task_id, call_id))
                return result
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
        finally:
            conn.close()

    def _depth(self, conn) -> int:
        row = conn.execute("SELECT depth FROM task WHERE id=?",
                           (self.task_id,)).fetchone()
        return row[0] if row else 0

    def _read_transcript(self, sid: str) -> str:
        from .supervisor import transcript_path
        with database(self.db_path) as conn:
            path = transcript_path(conn, sid, TRANSCRIPTS)
            if path:
                return path.read_text(errors="replace")
        return "(no transcript)"

    def _send_to_session(self, sid: str, text: str) -> str:
        from .supervisor import send_to_session
        send_to_session(self.db_path, sid, text)
        return json.dumps({"sent": True})

    # ── the agent loop (mirrors aiko.agent.Orchestrator.step) ───

    def step(self, user_text: str, max_tool_rounds: int = 12) -> str:
        if len(self.history) == 1:
            self.history.append({"role": "user", "content": user_text})
            self._checkpoint()
        for _ in range(max_tool_rounds):
            self._check_pause()
            # Resume any recorded model tool calls before making another request.
            assistant = next((m for m in reversed(self.history) if m["role"] == "assistant"), None)
            if assistant and assistant.get("tool_calls"):
                done = {m.get("tool_call_id") for m in self.history if m["role"] == "tool"}
                for call in assistant["tool_calls"]:
                    if call["id"] in done:
                        continue
                    self._check_pause()
                    fn = call["function"]
                    result_text = self._execute(fn["name"], json.loads(fn["arguments"]), call["id"])
                    self.history.append({"role": "tool", "tool_call_id": call["id"], "content": result_text})
                    self._checkpoint()
            if self.history[-1]["role"] == "assistant" and not self.history[-1].get("tool_calls"):
                return self.history[-1]["content"]
            self._check_pause()
            result = self.brain.complete(self.history, tools=_TOOL_DEFS)
            calls = result["tool_calls"]
            if not calls:
                reply = result["content"].strip()
                self.history.append({"role": "assistant", "content": reply})
                self._checkpoint()
                return reply
            self.history.append({
                "role": "assistant",
                "content": result["content"] or "",
                "tool_calls": [
                    {"id": f"call-{uuid.uuid4().hex}", "type": "function",
                     "function": {"name": c["name"],
                                  "arguments": json.dumps(c["arguments"])}}
                    for i, c in enumerate(calls)
                ],
            })
            self._checkpoint()
        # A tool-round limit is not completion. Continue the saved pass later.
        raise Deferred("tool round limit")


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
