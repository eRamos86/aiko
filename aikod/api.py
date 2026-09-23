"""FastAPI daemon surface. Auth = Nova JWT (ADR-009): HS256, payload {id, email}."""
import time
import uuid
from pathlib import Path

import jwt as pyjwt
from fastapi import Depends, FastAPI, Header, HTTPException

from .db import connect, hash_token
from .events import append, get_for_session


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
    def get_transcript(sid: str, tail: int = 60, user=Depends(require_nova_user)):
        """Tail bytes of a session's transcript (for TUI attach, ADR-010)."""
        from pathlib import Path as _P
        for candidate in (_P.home() / ".aikod" / "transcripts").glob(f"{sid}*"):
            if candidate.is_file():
                text = candidate.read_text(errors="replace")
                return {"session_id": sid, "file": candidate.name,
                        "tail": text[-(tail * 60):]}  # ~tail lines of chars
        raise HTTPException(404, f"no transcript for {sid}")

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
