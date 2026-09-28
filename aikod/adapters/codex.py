import subprocess
import os
from pathlib import Path

from .base import AdapterManifest, ModelCapability
from .tmux import TmuxAdapter

CODEX_AUTH = Path(os.environ.get("CODEX_AUTH_PATH", str(Path.home() / ".codex" / "auth.json")))

MANIFEST = AdapterManifest(
    provider_id="codex",
    one_shot=True, resume=True, fork=True, structured_output=True,
    control_daemon="tmux-bridge", concurrency_limit=3,
    quota_model="estimated",
    sandbox_tiers=["read-only", "workspace-write", "danger-full-access"],
    health_signals=["exit_code", "stderr_patterns"],
    models=[
        ModelCapability("gpt-5.5", "codex", 0.005, 500,
                        {"research": 0.7, "plan": 0.75, "implement": 0.95, "debug": 0.85, "review": 0.85, "write_docs": 0.7}),
    ],
)


class CodexAdapter(TmuxAdapter):
    manifest = MANIFEST

    def health(self) -> dict:
        try:
            r = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
            auth_ok = Path(os.environ.get("CODEX_AUTH_PATH", str(CODEX_AUTH))).exists()
            return {"ok": r.returncode == 0 and auth_ok, "version": r.stdout.strip(), "auth_present": auth_ok}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def spawn(self, task: dict, bundle_path: str) -> str:
        return self.launch(task, bundle_path, ["codex", "exec", "--sandbox", "workspace-write",
                           "--skip-git-repo-check", "--model",
                           task.get("model", MANIFEST.models[0].name)])
