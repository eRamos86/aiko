"""Local provider adapters backed by durable direct tmux sessions."""
from pathlib import Path

from .base import LocalAdapterManifest
from ..session_manager import SessionManager


class _LocalAdapter:
    command: str
    one_shot: list[str]

    def _manager(self):
        # Importing the adapter registry must not create a database.
        manager = SessionManager()
        provider = self.manifest.provider_id
        manager.agents.setdefault(provider, {"name": provider, "command": self.command,
                                            "one_shot": self.one_shot})
        return manager

    def health(self) -> dict:
        from shutil import which
        return {"ok": which(self.command) is not None and which("tmux") is not None,
                "where": which(self.command), "tmux": which("tmux")}

    def spawn(self, task: dict, bundle_path: str) -> str:
        prompt = task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text()
        row = self._manager().dispatch(
            self.manifest.provider_id,
            task.get("worktree_path") or task.get("cwd") or str(Path.cwd()),
            prompt, repo_direct=task.get("repo_direct") is True)
        return row["id"]

    def status(self, sid: str) -> dict:
        try:
            row = self._manager().get(sid)
        except ValueError:
            return {"alive": False, "state": "unknown"}
        return {"alive": row["state"] == "running", "state": row["state"],
                "exit_code": row["exit_code"], "session_id": sid}

    def send_input(self, sid: str, text: str) -> None:
        result = self._manager().send_input(sid, text)
        if "error" in result:
            raise RuntimeError(result["error"])

    def kill(self, sid: str) -> None:
        self._manager().stop(sid)


class LocalHermesAdapter(_LocalAdapter):
    manifest = LocalAdapterManifest(
        provider_id="hermes",
        models=["gpt-5.6-luna", "nvidia/nemotron-3-ultra-550b-a55b"],
        concurrency_limit=2,
    )
    command = "hermes"
    one_shot = ["chat", "-q", "{prompt}"]


class LocalCodexAdapter(_LocalAdapter):
    manifest = LocalAdapterManifest(provider_id="codex", models=["gpt-5.5"],
                                    concurrency_limit=2)
    command = "codex"
    one_shot = ["exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "{prompt}"]


class LocalAGYAdapter(_LocalAdapter):
    manifest = LocalAdapterManifest(provider_id="antigravity",
                                    models=["gemini-3-pro", "gemini-3-flash"],
                                    concurrency_limit=2)
    command = "agy"
    one_shot = ["--output-format", "text", "--print={prompt}"]


LOCAL_ADAPTERS = {
    "hermes": LocalHermesAdapter(),
    "codex": LocalCodexAdapter(),
    "antigravity": LocalAGYAdapter(),
}
