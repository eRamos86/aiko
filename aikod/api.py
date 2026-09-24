"""FastAPI daemon surface. Auth = Nova JWT (ADR-009): HS256, payload {id, email}."""
import time
import uuid
from pathlib import Path

import jwt as pyjwt
from fastapi import Depends, FastAPI, Header, HTTPException

from .db import connect, hash_token
from .events import append, get_for_session


def build_story(conn, sid: str) -> dict | None:
    """Assemble the delegation story for a session id (or its task id).

    Sessions are keyed by task id in v0.1, but handle both: resolve the
    session row first, fall back to treating sid as a task id.
    """
    sess = conn.execute("SELECT task_id FROM session WHERE id=?", (sid,)).fetchone()
    task_id = sess[0] if sess else sid
    task = conn.execute(
        "SELECT goal_id, title, spec, state, assigned_provider, assigned_model "
        "FROM task WHERE id=?", (task_id,)).fetchone()
    if not task:
        return None
    goal_id, title, spec, state, prov, model = task
    goal = conn.execute(
        "SELECT text, status, created_by_device FROM goal WHERE id=?",
        (goal_id,)).fetchone()
    tasks = conn.execute(
        "SELECT id, parent_task_id, title, state, assigned_provider, "
        "assigned_model, created_at FROM task WHERE goal_id=? ORDER BY created_at",
        (goal_id,)).fetchall()
    sessions = conn.execute(
        "SELECT id, task_id, provider_id, model, state FROM session "
        "WHERE task_id IN (SELECT id FROM task WHERE goal_id=?) ORDER BY id",
        (goal_id,)).fetchall()
    decisions: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT task_id, chosen_provider, chosen_model, yaml_scores_json, "
        "reasoning FROM routing_decision WHERE task_id IN "
        "(SELECT id FROM task WHERE goal_id=?)", (goal_id,)).fetchall():
        decisions[r[0]] = {"provider": r[1], "model": r[2], "reasoning": r[4]}
    orch = {r[0]: r[1] for r in conn.execute(
        "SELECT task_id, reply FROM orchestrator_run WHERE task_id IN "
        "(SELECT id FROM task WHERE goal_id=?)", (goal_id,)).fetchall()}
    return {
        "goal": {"id": goal_id,
                 "text": goal[0] if goal else spec,
                 "status": goal[1] if goal else state,
                 "by": goal[2] if goal else "?"},
        "tasks": [{"id": t[0], "parent": t[1], "title": t[2], "state": t[3],
                   "provider": t[4], "model": t[5]} for t in tasks],
        "sessions": [{"id": s[0], "task_id": s[1], "provider": s[2],
                      "model": s[3], "state": s[4]} for s in sessions],
        "decisions": decisions,
        "orchestrator_replies": orch,
    }


def create_app(db_path, nova_jwt_secret: str | None = None) -> FastAPI:
    import os
    secret = nova_jwt_secret or os.environ.get("NOVA_JWT_SECRET", "")
    app = FastAPI(title="aikod", version="0.1.0")
    db_path = Path(db_path)

    def require_nova_user(authorization: str | None = Header(default=None)) -> dict:
        """Verify a Nova-issued JWT. Returns the payload {id, email}."""
        if not secret:
            raise HTTPException(500, "NOVA_JWT_SECRET not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing or bad token format")
        token = authorization[len("Bearer "):]
        try:
            payload = pyjwt.decode(token, secret, algorithms=["HS256"])
        except pyjwt.ExpiredSignatureError:
            raise HTTPException(401, "token expired")
        except pyjwt.InvalidTokenError:
            raise HTTPException(401, "invalid token")
        if "id" not in payload or "email" not in payload:
            raise HTTPException(401, "token missing id/email claims")
        return payload

    @app.get("/health")
    def health():
        from .adapters.registry import ADAPTERS
        from .router import ROUTER_YAML_PATH, OBSERVED_PATH
        return {
            "status": "ok",
            "adapters": {k: v.health() for k, v in ADAPTERS.items()},
            "router_yaml_present": ROUTER_YAML_PATH.exists(),
            "observed_present": OBSERVED_PATH.exists(),
        }

    @app.post("/goals")
    def create_goal(body: dict, user=Depends(require_nova_user)):
        conn = connect(db_path)
        goal_id = f"goal-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO goal VALUES (?, ?, ?, 'queued', ?, ?)",
            (goal_id, body["text"],
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             user["email"], body.get("locality", "auto")),
        )
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO task(id, goal_id, title, spec, state, fingerprint) VALUES (?, ?, ?, ?, 'ready', ?)",
            (task_id, goal_id, body["text"][:80], body["text"], uuid.uuid4().hex),
        )
        append(conn, task_id, "task.created", {"spec": body["text"], "by": user["email"]})
        return {"goal_id": goal_id, "task_id": task_id}

    @app.get("/sessions")
    def list_sessions(user=Depends(require_nova_user)):
        conn = connect(db_path)
        cur = conn.execute(
            "SELECT s.id, s.task_id, s.provider_id, s.state, t.title, t.state "
            "FROM session s JOIN task t ON s.task_id = t.id "
            "ORDER BY s.id"
        )
        return {"sessions": [
            {"id": r[0], "task_id": r[1], "provider": r[2], "session_state": r[3],
             "title": r[4], "task_state": r[5]}
            for r in cur.fetchall()
        ]}

    @app.get("/sessions/{sid}/events")
    def get_events(sid: str, user=Depends(require_nova_user)):
        conn = connect(db_path)
        return {"events": get_for_session(conn, sid)}

    @app.get("/sessions/{sid}/transcript")
    def get_transcript(sid: str, tail: int = 0, user=Depends(require_nova_user)):
        """Transcript text. tail=0 (default) → FULL transcript; tail=N → last N chars."""
        from pathlib import Path as _P
        for candidate in (_P.home() / ".aikod" / "transcripts").glob(f"{sid}*"):
            if candidate.is_file():
                text = candidate.read_text(errors="replace")
                if tail and tail > 0:
                    text = text[-tail:]
                return {"session_id": sid, "file": candidate.name,
                        "tail": text, "full": not tail}
        raise HTTPException(404, f"no transcript for {sid}")

    @app.get("/sessions/{sid}/story")
    def session_story(sid: str, user=Depends(require_nova_user)):
        """The delegation story for the goal this session belongs to:
        goal text, task tree, routing decisions (who/why/score), and every
        parallel session — so the client can render the whole narrative."""
        conn = connect(db_path)
        story = build_story(conn, sid)
        if not story:
            raise HTTPException(404, f"no story for {sid}")
        return story

    @app.post("/sessions/{sid}/send")
    def send_to_session(sid: str, body: dict, user=Depends(require_nova_user)):
        """Send text into a live worker's tmux pane (ADR-010 attach)."""
        import subprocess as _sp
        r = _sp.run(["tmux", "has-session", "-t", f"aiko-{sid}"],
                    capture_output=True)
        if r.returncode != 0:
            raise HTTPException(409, f"session aiko-{sid} not alive (exited workers can't receive input)")
        text = str(body.get("text", ""))
        if not text.strip():
            raise HTTPException(400, "empty text")
        _sp.run(["tmux", "send-keys", "-t", f"aiko-{sid}", "-l", text],
                check=True)
        _sp.run(["tmux", "send-keys", "-t", f"aiko-{sid}", "Enter"],
                check=True)
        conn = connect(db_path)
        append(conn, sid, "session.user_input",
               {"text": text[:500], "by": user["email"]})
        return {"sent": True, "session_id": sid}

    @app.get("/goals/{gid}")
    def get_goal(gid: str, user=Depends(require_nova_user)):
        conn = connect(db_path)
        goal = conn.execute("SELECT * FROM goal WHERE id=?", (gid,)).fetchone()
        if not goal:
            raise HTTPException(404, "goal not found")
        tasks = conn.execute("SELECT id, title, state, assigned_provider, assigned_model "
                             "FROM task WHERE goal_id=? ORDER BY created_at", (gid,)).fetchall()
        return {
            "goal": {"id": goal[0], "text": goal[1], "status": goal[3]},
            "tasks": [{"id": t[0], "title": t[1], "state": t[2],
                       "provider": t[3], "model": t[4]} for t in tasks],
        }

    return app
