import sqlite3
from contextlib import contextmanager
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
    c = sqlite3.connect(path, isolation_level=None, timeout=30)
    c.executescript(SCHEMA)
    # Additive migrations: preserve old rows and the legacy orchestrator ledger.
    with transaction(c):
        for table, columns in {
            "session": {"tmux_socket": "TEXT", "exit_code": "INTEGER", "error": "TEXT"},
        }.items():
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        c.execute("CREATE TABLE IF NOT EXISTS orchestrator_run ("
                  "task_id TEXT PRIMARY KEY, reply TEXT, ts TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS orchestrator_job ("
                  "task_id TEXT PRIMARY KEY REFERENCES task(id), "
                  "state TEXT NOT NULL DEFAULT 'pending', history TEXT, "
                  "reply TEXT, error TEXT, retry_at REAL NOT NULL DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS orchestrator_effect ("
                  "task_id TEXT NOT NULL, call_id TEXT NOT NULL, result TEXT NOT NULL, "
                  "PRIMARY KEY(task_id, call_id))")
        c.execute("CREATE TABLE IF NOT EXISTS daemon_control ("
                  "id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0)")
        c.execute("INSERT OR IGNORE INTO daemon_control(id) VALUES (1)")
    return c


@contextmanager
def transaction(conn):
    """Serialize short state transitions across connections/processes."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def database(path):
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()
