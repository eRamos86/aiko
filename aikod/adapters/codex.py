import subprocess
import shlex
import os
from pathlib import Path

from .base import AdapterManifest, ModelCapability

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


class CodexAdapter:
    manifest = MANIFEST

    def health(self) -> dict:
        try:
            r = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
            auth_ok = CODEX_AUTH.exists()
            return {"ok": r.returncode == 0 and auth_ok, "version": r.stdout.strip(), "auth_present": auth_ok}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def spawn(self, task: dict, bundle_path: str) -> str:
        session_id = task["id"]
        transcript = Path.home() / ".aikod" / "transcripts" / f"{session_id}.codex.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)

        # `codex exec` requires a PTY per Aiko Platform Planning §5.1; wrap in tmux.
        prompt_file = Path(f"/tmp/aikod/{session_id}.prompt.md")
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text())

        tmux_cmd = (
            f"tmux new-session -d -s aiko-{session_id} -c /tmp/aikod "
            f"'codex exec --sandbox workspace-write --skip-git-repo-check "
            f"--model {shlex.quote(task.get('model', MANIFEST.models[0].name))} "
            f"\"$(cat {prompt_file})\" "
            f"2>&1 | tee {transcript}'"
        )
        subprocess.run(tmux_cmd, shell=True, check=True)
        return session_id

    def status(self, session_id: str) -> dict:
        r = subprocess.run(["tmux", "has-session", "-t", f"aiko-{session_id}"], capture_output=True)
        return {"alive": r.returncode == 0}

    def send_input(self, session_id: str, text: str) -> None:
        # v0.1: forward text into the running pane via tmux send-keys.
        # Better flow lands with app-server integration in v0.3 (per plan).
        subprocess.run(
            ["tmux", "send-keys", "-t", f"aiko-{session_id}", "-l", text, "Enter"],
            check=True,
        )

    def kill(self, session_id: str) -> None:
        subprocess.run(["tmux", "kill-session", "-t", f"aiko-{session_id}"], capture_output=True)
