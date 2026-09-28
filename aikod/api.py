"""Daemon API: explicit bearer token or existing Nova JWT; closed by default."""
import time
import uuid
import secrets
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import jwt as pyjwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .db import connect, database
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


def create_app(db_path, nova_jwt_secret: str | None = None, auth_token: str | None = None) -> FastAPI:
    import os
    secret = nova_jwt_secret if nova_jwt_secret is not None else os.environ.get("NOVA_JWT_SECRET", "")
    daemon_token = auth_token if auth_token is not None else os.environ.get("AIKOD_AUTH_TOKEN", "")
    app = FastAPI(title="aikod", version="0.1.0")
    db_path = Path(db_path)

    def require_nova_user(authorization: str | None = Header(default=None)) -> dict:
        """Verify a Nova-issued JWT. Returns the payload {id, email}."""
        if not secret and not daemon_token:
            raise HTTPException(503, "daemon authentication is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing or bad token format")
        token = authorization[len("Bearer "):]
        if daemon_token and secrets.compare_digest(token.encode(), daemon_token.encode()):
            return {"id": "daemon", "email": "daemon-token"}
        if not secret:
            raise HTTPException(401, "invalid token")
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
        try:
            package_version = version("aiko")
        except PackageNotFoundError:
            package_version = "unknown"
        return {
            "status": "ok",
            "version": package_version,
            "runtime": str(Path(sys.prefix).resolve()),
            "runtime_path": str(Path(__file__).resolve().parent),
            "control_protocol": 1,
            "adapters": list(ADAPTERS),
            "router_yaml_present": ROUTER_YAML_PATH.exists(),
            "observed_present": OBSERVED_PATH.exists(),
        }

    @app.get("/control/status")
    def control_status(user=Depends(require_nova_user)):
        from .control import update_status
        return update_status(db_path)

    @app.post("/control/quiesce")
    def quiesce(user=Depends(require_nova_user)):
        from .control import update_status
        return update_status(db_path, pause=True)

    @app.post("/control/resume")
    def resume(user=Depends(require_nova_user)):
        from .control import update_status
        return update_status(db_path, pause=False)

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

    @app.get("/approvals")
    def list_approvals(pending: bool = True, user=Depends(require_nova_user)):
        """Return approval gates for a client inbox, newest request first.

        Approvals deliberately remain daemon state.  A desktop client can close,
        reconnect, or be replaced without losing an irreversible-action gate.
        """
        conn = connect(db_path)
        query = ("SELECT a.id,a.task_id,a.action,a.payload_digest,a.requested_at,"
                 "a.decided_at,a.decision,t.title FROM approval a "
                 "JOIN task t ON t.id=a.task_id")
        if pending:
            query += " WHERE a.decision IS NULL"
        rows = conn.execute(query + " ORDER BY a.requested_at DESC").fetchall()
        return {"approvals": [
            {"id": row[0], "task_id": row[1], "action": row[2],
             "payload_digest": row[3], "requested_at": row[4],
             "decided_at": row[5], "decision": row[6], "title": row[7]}
            for row in rows
        ]}

    @app.post("/approvals/{approval_id}/decision")
    def decide_approval(approval_id: str, body: dict, user=Depends(require_nova_user)):
        """Persist a human approval decision exactly once.

        Workers/schedulers own the action-specific transition after observing this
        durable decision; the API must not guess whether an approval permits a
        merge, deployment, or another irreversible action.
        """
        decision = body.get("decision")
        if decision not in {"granted", "denied"}:
            raise HTTPException(422, "decision must be granted or denied")
        conn = connect(db_path)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        changed = conn.execute(
            "UPDATE approval SET decision=?,decided_at=?,decided_by=? "
            "WHERE id=? AND decision IS NULL",
            (decision, now, user["email"], approval_id),
        ).rowcount
        if not changed:
            exists = conn.execute("SELECT 1 FROM approval WHERE id=?", (approval_id,)).fetchone()
            if not exists:
                raise HTTPException(404, f"unknown approval {approval_id}")
            raise HTTPException(409, "approval was already decided")
        return {"id": approval_id, "decision": decision, "decided_at": now}

    @app.get("/sessions/{sid}/events")
    def get_events(sid: str, user=Depends(require_nova_user)):
        conn = connect(db_path)
        return {"events": get_for_session(conn, sid)}

    @app.get("/sessions/{sid}/transcript")
    def get_transcript(sid: str, tail: int = 0, user=Depends(require_nova_user)):
        """Transcript text. tail=0 (default) → FULL transcript; tail=N → last N chars."""
        from .supervisor import transcript_path
        with database(db_path) as conn:
            candidate = transcript_path(conn, sid)
        if candidate:
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
        from .supervisor import send_to_session as send
        text = str(body.get("text", ""))
        if not text.strip():
            raise HTTPException(400, "empty text")
        try:
            send(db_path, sid, text)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
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

    @app.get("/audit/docs")
    async def audit_docs(
        max_docs: int = Query(40, ge=1, le=200),
        auto_fix: bool = Query(True),
        write_report: bool = Query(True),
        user=Depends(require_nova_user),
    ):
        """Proxy to the standalone docs-auditor service (port 4012).
        The auditor is NOT part of aikod — this is a convenience route for
        clients that already hold Nova JWTs."""
        import httpx as _hx
        try:
            r = _hx.get("http://127.0.0.1:4012/audit",
                        params={"max_docs": max_docs, "auto_fix": auto_fix,
                                "write_report": write_report},
                        timeout=_hx.Timeout(600, connect=5))
            return r.json()
        except _hx.ConnectError:
            return {"error": "docs-auditor service not running "
                             "(docsauditor serve on port 4012)"}
        except Exception as e:
            return {"error": str(e)}

    return app
