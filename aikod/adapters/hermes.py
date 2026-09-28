import subprocess

from .base import AdapterManifest, ModelCapability
from .tmux import TmuxAdapter


MANIFEST = AdapterManifest(
    provider_id="hermes",
    one_shot=True, resume=True, fork=False, structured_output=False,
    control_daemon="tmux-bridge", concurrency_limit=4,
    quota_model="unmetered",
    sandbox_tiers=["workspace-write", "read-only"],
    health_signals=["exit_code", "stderr_patterns", "per_model_429"],
    models=[
        # Server hermes runs via openai-codex (ChatGPT subscription backend).
        # Model availability is whatever the subscription entitles; the daemon
        # spawns WITHOUT -m so hermes uses its configured default (gpt-5.6-luna).
        # Router scores below approximate subscription-model capability.
        ModelCapability("gpt-5.6-luna", "hermes", 0.0, None,
                        {"research": 0.85, "plan": 0.6, "implement": 0.5, "debug": 0.7, "review": 0.75, "write_docs": 0.8}),
    ],
)


class HermesAdapter(TmuxAdapter):
    manifest = MANIFEST

    def health(self) -> dict:
        try:
            r = subprocess.run(["hermes", "--version"], capture_output=True, text=True, timeout=5)
            return {"ok": r.returncode == 0, "version": r.stdout.strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def spawn(self, task: dict, bundle_path: str) -> str:
        # Do NOT pass -m: server hermes owns its model config (openai-codex
        # backend rejects models the subscription doesn't entitle). The router's
        # chosen model is recorded in the routing decision; execution uses the
        # server's configured default.
        return self.launch(task, bundle_path, ["hermes", "chat", "-q"])
