import subprocess
import shlex
from pathlib import Path

from .base import AdapterManifest, ModelCapability


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


class HermesAdapter:
    manifest = MANIFEST

    def health(self) -> dict:
        try:
            r = subprocess.run(["hermes", "--version"], capture_output=True, text=True, timeout=5)
            return {"ok": r.returncode == 0, "version": r.stdout.strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def spawn(self, task: dict, bundle_path: str) -> str:
        session_id = task["id"]
        # Do NOT pass -m: server hermes owns its model config (openai-codex
        # backend rejects models the subscription doesn't entitle). The router's
        # chosen model is recorded in the routing decision; execution uses the
        # server's configured default.
        prompt_file = Path(f"/tmp/aikod/{session_id}.prompt.md")
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text())

        transcript = Path.home() / ".aikod" / "transcripts" / f"{session_id}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)

        tmux_cmd = (
            f"tmux new-session -d -s aiko-{session_id} -c /tmp/aikod "
            f"'hermes chat -q \"$(cat {prompt_file})\" "
            f"2>&1 | tee {transcript}'"
        )
        subprocess.run(tmux_cmd, shell=True, check=True)
        return session_id

    def status(self, session_id: str) -> dict:
        r = subprocess.run(
            ["tmux", "has-session", "-t", f"aiko-{session_id}"],
            capture_output=True,
        )
        return {"alive": r.returncode == 0}

    def send_input(self, session_id: str, text: str) -> None:
        subprocess.run(
            ["tmux", "send-keys", "-t", f"aiko-{session_id}", "-l", text, "Enter"],
            check=True,
        )

    def kill(self, session_id: str) -> None:
        subprocess.run(
            ["tmux", "kill-session", "-t", f"aiko-{session_id}"],
            capture_output=True,
        )
