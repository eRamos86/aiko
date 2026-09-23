import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS goal (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  created_by_device TEXT NOT NULL,
  locality TEXT NOT NULL DEFAULT 'auto'   -- 'local' | 'server' | 'auto'
);

CREATE TABLE IF NOT EXISTS task (
  id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  parent_task_id TEXT,
  title TEXT NOT NULL,
  spec TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued',  -- queued, planning, ready, running, completed, failed, cancelled, awaiting_approval
  depth INTEGER NOT NULL DEFAULT 0,
  budget_tokens INTEGER,
  fingerprint TEXT NOT NULL UNIQUE,
  assigned_provider TEXT,
  assigned_model TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  FOREIGN KEY (goal_id) REFERENCES goal(id)
);

CREATE TABLE IF NOT EXISTS session (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  model TEXT,
  adapter_config TEXT,
  worktree_path TEXT,
  transcript_path TEXT,
  state TEXT NOT NULL DEFAULT 'live',    -- live | exited
  FOREIGN KEY (task_id) REFERENCES task(id)
);

CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider (
  id TEXT PRIMARY KEY,
  manifest_json TEXT NOT NULL,
  health_json TEXT,
  concurrency_state TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS routing_decision (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  chosen_provider TEXT NOT NULL,
  chosen_model TEXT NOT NULL,
  yaml_scores_json TEXT NOT NULL,
  observed_weights_json TEXT NOT NULL,
  reasoning TEXT NOT NULL,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_token (
  id TEXT PRIMARY KEY,
  device_name TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS approval (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  action TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  decision TEXT,           -- 'granted' | 'denied'
  decided_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_session ON event(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_task_goal ON task(goal_id);
CREATE INDEX IF NOT EXISTS idx_task_state ON task(state);
CREATE INDEX IF NOT EXISTS idx_session_task ON session(task_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit txns managed by callers
    c.executescript(SCHEMA)
    return c


def hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()
