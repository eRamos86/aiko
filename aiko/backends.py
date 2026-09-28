"""Aiko backends — where dispatched work actually runs.

Two modes (config `backend:`):
  - local:   the orchestrator spawns configured `agents:` directly, no server.
  - servers: dispatch to one or more aikod daemons (config `servers:` list).

The Orchestrator only talks to a Backend — it never knows which mode is active.
Multi-server: each server is addressable by name; dispatch takes optional target.
"""
import json
from pathlib import Path

import httpx

from .session_manager import SessionManager


# ── helpers ─────────────────────────────────────────────────────

def _load_cfg() -> dict:
    import yaml
    p = Path.home() / ".aiko" / "config.yaml"
    try:
        return yaml.safe_load(p.read_text()) or {}
    except FileNotFoundError:
        return {}


def _expand(value: str) -> str:
    import os
    import re
    if not isinstance(value, str):
        return value
    return re.sub(r"\$\{([A-Z0-9_]+)\}",
                  lambda m: os.environ.get(m.group(1), ""), value)


# ── Backend interface (duck-typed) ──────────────────────────────

class Backend:
    """What the orchestrator's tools call. Subclasses implement the modes."""
    name = "backend"

    def list_targets(self) -> list[dict]:
        raise NotImplementedError

    def dispatch(self, text: str, target: str | None = None) -> dict:
        raise NotImplementedError

    def task_status(self, task_id: str, target: str | None = None) -> dict:
        raise NotImplementedError

    def list_sessions(self) -> list[dict]:
        raise NotImplementedError

    def read_transcript(self, session_id: str, tail: int | None = None) -> str:
        raise NotImplementedError

    def session_story(self, session_id: str) -> dict:
        raise NotImplementedError

    def send_to_session(self, session_id: str, text: str) -> dict:
        raise NotImplementedError


# ── Local backend — durable direct tmux workers ──────────────────

class LocalBackend(Backend):
    name = "local"

    def __init__(self, config=None):
        self.cfg = _load_cfg() if config is None else config
        self.agents = {a["name"]: a for a in self.cfg.get("agents", [])}
        self.sessions = SessionManager(self.cfg)

    def list_targets(self) -> list[dict]:
        return [{"target": "local", "agents": sorted(self.agents.keys())}]

    def dispatch(self, text: str, target: str | None = None, *, cwd=None,
                 repo_direct=False) -> dict:
        # pick agent: prefer hermes → codex → antigravity → first configured
        for preferred in ("hermes", "codex", "antigravity"):
            if preferred in self.agents:
                agent = self.agents[preferred]
                break
        else:
            if not self.agents:
                return {"error": "no agents configured in ~/.aiko/config.yaml"}
            agent = next(iter(self.agents.values()))

        try:
            session = self.sessions.dispatch(agent["name"], cwd or Path.cwd(), text,
                                             target=target or "local", repo_direct=repo_direct)
        except (ValueError, RuntimeError, OSError) as exc:
            return {"error": str(exc)}
        return {**session, "dispatched": True, "task_id": session["id"]}

    def task_status(self, task_id: str, target: str | None = None) -> dict:
        try:
            session = self.sessions.get(task_id)
        except ValueError:
            return {"error": "unknown task"}
        if target is not None and target != session["target"]:
            return {"error": "session target mismatch"}
        status = {**session, "task_id": task_id}
        # learning: on completion, fold the outcome into the feedback ledger
        if (session["state"] in {"completed", "failed"}
                and self.sessions.claim_outcome(task_id)):
            try:
                from .feedback import record_outcome
                record_outcome(session["agent"], "implement", session["state"] == "completed")
            except Exception:
                pass
        return status

    def list_sessions(self) -> list[dict]:
        return self.sessions.list()

    def read_transcript(self, session_id: str, tail: int | None = None) -> str:
        try:
            return self.sessions.read_transcript(session_id, tail)
        except ValueError:
            return f"(no transcript for {session_id})"

    def send_to_session(self, session_id: str, text: str) -> dict:
        try:
            return self.sessions.send_input(session_id, text)
        except (ValueError, RuntimeError, OSError) as exc:
            return {"error": str(exc)}

    def session_story(self, session_id: str) -> dict:
        try:
            session = self.sessions.get(session_id)
        except ValueError:
            return {"error": "unknown session"}
        return {"goal": {"id": session_id, "text": session["title"],
                         "status": session["state"], "by": "local"},
                "tasks": [{**session, "parent": None}], "sessions": [session],
                "decisions": {}, "orchestrator_replies": {}}


# ── Server backend — one or more aikod daemons ───────────────────

class ServerBackend(Backend):
    name = "servers"

    def __init__(self):
        self.cfg = _load_cfg()
        self.servers = {s["name"]: s for s in self.cfg.get("servers", [])}
        self.default = next(iter(self.servers), None)

    # -- pick server + auth headers --
    def _server(self, target: str | None = None):
        name = target or self.default
        if name not in self.servers:
            raise RuntimeError(f"unknown server '{name}' — configured: {list(self.servers)}")
        return name, self.servers[name]

    def _headers(self, server: dict) -> dict:
        auth = server.get("auth", "none")
        if auth == "token":
            token = server.get("auth_token") or _expand(
                "${AIKO_TOKEN_%s}" % server["name"].upper())
            return {"Authorization": f"Bearer {token}"}
        if auth == "nova_jwt":
            from .auth import load_token
            tok = load_token()
            if not tok:
                raise RuntimeError("not logged in — run `aiko login`")
            return {"Authorization": f"Bearer {tok}"}
        return {}

    def _url(self, server: dict, path: str) -> str:
        return f"{server['url'].rstrip('/')}{path}"

    def _get(self, path: str, target: str | None = None, params: dict | None = None):
        name, server = self._server(target)
        r = httpx.get(self._url(server, path), headers=self._headers(server),
                      params=params, timeout=30)
        r.raise_for_status()
        return name, r.json()

    def _post(self, path: str, body: dict, target: str | None = None):
        name, server = self._server(target)
        r = httpx.post(self._url(server, path), headers=self._headers(server),
                       json=body, timeout=60)
        r.raise_for_status()
        return name, r.json()

    # -- interface --
    def list_targets(self) -> list[dict]:
        targets = []
        for name, s in self.servers.items():
            try:
                h = httpx.get(f"{s['url'].rstrip('/')}/health", timeout=10).json()
                targets.append({"target": name, "status": h.get("status", "?"),
                                "adapters": {k: v.get("ok") for k, v in h.get("adapters", {}).items()}})
            except Exception as e:
                targets.append({"target": name, "status": "unreachable", "error": str(e)[:80]})
        return targets

    def dispatch(self, text: str, target: str | None = None) -> dict:
        _, result = self._post("/goals", {"text": text, "locality": "server"}, target)
        result["target"] = target or self.default
        return result

    def task_status(self, task_id: str, target: str | None = None) -> dict:
        _, sessions = self._get("/sessions", target)
        match = [s for s in sessions.get("sessions", []) if s["id"].startswith(task_id)]
        if not match:
            return {"state": "unknown-or-completed"}
        s = match[0]
        return {"state": s["session_state"], "title": s["title"], "provider": s["provider"]}

    def list_sessions(self) -> list[dict]:
        rows = []
        for name in self.servers:
            try:
                _, sessions = self._get("/sessions", name)
                for s in sessions.get("sessions", []):
                    s["server"] = name
                    rows.append(s)
            except Exception:
                continue
        return rows

    def read_transcript(self, session_id: str, tail: int | None = None) -> str:
        _, data = self._get(f"/sessions/{session_id}/transcript",
                            params={"tail": tail} if tail else None)
        return data.get("tail", "")

    def session_story(self, session_id: str) -> dict:
        _, data = self._get(f"/sessions/{session_id}/story")
        return data

    def send_to_session(self, session_id: str, text: str) -> dict:
        _, data = self._post(f"/sessions/{session_id}/send", {"text": text})
        return data


# ── Composite: local + every server, all addressable ────────────

class AllBackends(Backend):
    """What `aiko` actually uses: local agents + every configured server.
    `local` is always a valid target; each server by name."""

    def __init__(self):
        self.local = LocalBackend()
        self.remote = ServerBackend() if _load_cfg().get("servers") else None

    def list_targets(self) -> list[dict]:
        targets = self.local.list_targets()
        if self.remote:
            targets += self.remote.list_targets()
        return targets

    def dispatch(self, text: str, target: str | None = None) -> dict:
        if target == "local" or (target is None and not self.remote):
            return self.local.dispatch(text)
        if target is None and self.remote:
            # default: server for heavy/long work (configurable default_target)
            default_target = _load_cfg().get("default_target", "server")
            if default_target == "local":
                return self.local.dispatch(text)
            return self.remote.dispatch(text)
        if self.remote:
            return self.remote.dispatch(text, target)
        return {"error": f"no server '{target}' configured"}

    def task_status(self, task_id: str, target: str | None = None) -> dict:
        if target == "local" or task_id.startswith("local-"):
            return self.local.task_status(task_id)
        if self.remote:
            return self.remote.task_status(task_id, target)
        return {"state": "unknown"}

    def list_sessions(self) -> list[dict]:
        rows = self.local.list_sessions()
        if self.remote:
            rows += self.remote.list_sessions()
        return rows

    def read_transcript(self, session_id: str, tail: int | None = None) -> str:
        if session_id.startswith("local-"):
            return self.local.read_transcript(session_id, tail=tail)
        if self.remote:
            return self.remote.read_transcript(session_id, tail=tail)
        return "(local session not found)"

    def session_story(self, session_id: str) -> dict:
        if session_id.startswith("local-"):
            return self.local.session_story(session_id)
        if self.remote:
            return self.remote.session_story(session_id)
        return {}

    def send_to_session(self, session_id: str, text: str) -> dict:
        if session_id.startswith("local-"):
            return self.local.send_to_session(session_id, text)
        if self.remote:
            return self.remote.send_to_session(session_id, text)
        return {"error": "no server configured"}


def get_backend() -> Backend:
    """Config decides: local-only users get just LocalBackend semantics via
    AllBackends with no servers; server users get the composite."""
    return AllBackends()
