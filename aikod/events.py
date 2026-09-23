"""Append-only event log helpers."""
import json
import sqlite3
import time


def append(conn: sqlite3.Connection, session_id: str | None, event_type: str,
           payload: dict, ts: str | None = None) -> int:
    if ts is None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM event WHERE session_id IS ?",
        (session_id,),
    ).fetchone()
    seq = seq_row[0]
    cur = conn.execute(
        "INSERT INTO event(session_id, seq, type, payload_json, ts) VALUES (?, ?, ?, ?, ?)",
        (session_id, seq, event_type, json.dumps(payload), ts),
    )
    return cur.lastrowid


def get_for_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cur = conn.execute(
        "SELECT seq, type, payload_json, ts FROM event WHERE session_id = ? ORDER BY seq",
        (session_id,),
    )
    return [{"seq": r[0], "type": r[1], "payload": json.loads(r[2]), "ts": r[3]} for r in cur]
