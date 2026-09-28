"""Durable conversation snapshots, separate from installed application code."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path


class ConversationStore:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".aiko" / "conversations.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS conversation "
                         "(id TEXT PRIMARY KEY, history TEXT NOT NULL, "
                         "updated REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def latest(self):
        with self.connect() as conn:
            row = conn.execute("SELECT id,history FROM conversation WHERE active=1 "
                               "ORDER BY updated DESC LIMIT 1").fetchone()
        if row:
            return row[0], json.loads(row[1])
        return uuid.uuid4().hex, []

    def save(self, cid: str, history: list[dict]):
        import time
        with self.connect() as conn:
            conn.execute("INSERT INTO conversation(id,history,updated) VALUES (?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET history=excluded.history, "
                         "updated=excluded.updated", (cid, json.dumps(history), time.time()))

    def archive(self, cid: str):
        with self.connect() as conn:
            conn.execute("UPDATE conversation SET active=0 WHERE id=?", (cid,))


def resumable_history(history: list[dict]) -> list[dict]:
    """Drop only an unfinished tool-call group after an interrupted client turn.

    Providers require every assistant tool call to have a corresponding reply.
    The persisted snapshot stays intact; this is a safe replay view.
    """
    for i, message in enumerate(history):
        calls = message.get("tool_calls") or []
        if not calls:
            continue
        expected = {call["id"] for call in calls}
        seen = set()
        for following in history[i + 1:]:
            if following.get("role") != "tool":
                break
            seen.add(following.get("tool_call_id"))
        if not expected.issubset(seen):
            return history[:i]
    return history
