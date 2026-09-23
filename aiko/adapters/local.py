"""Local (Mac-direct) provider adapters — D2's locality=local path.

Same Protocol as the server adapters, but each spawns the binary directly in a
subprocess on this machine. No tmux, no daemon hop.
"""
import subprocess
from pathlib import Path

from .base import LocalAdapterManifest


class LocalHermesAdapter:
    manifest = LocalAdapterManifest(
        provider_id="hermes",
        models=["gpt-5.6-luna", "nvidia/nemotron-3-ultra-550b-a55b"],
        concurrency_limit=2,
    )

    def health(self) -> dict:
        from shutil import which
        return {"ok": which("hermes") is not None, "where": which("hermes")}

    def spawn(self, task: dict, bundle_path: str) -> str:
        sid = task["id"]
        transcript = Path.home() / ".aiko" / "transcripts" / f"{sid}.hermes.local.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        prompt = task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text()
        cmd = ["hermes", "chat", "-q", prompt]
        proc = subprocess.Popen(cmd, stdout=transcript.open("w"), stderr=subprocess.STDOUT)
        self._proc = proc
        return sid

    def status(self, sid: str) -> dict:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            return {"alive": True, "pid": proc.pid}
        return {"alive": False}

    def send_input(self, sid: str, text: str) -> None:
        raise NotImplementedError("local hermes one-shot only")

    def kill(self, sid: str) -> None:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            proc.terminate()


class LocalCodexAdapter:
    manifest = LocalAdapterManifest(
        provider_id="codex",
        models=["gpt-5.5"],
        concurrency_limit=2,
    )

    def health(self) -> dict:
        from shutil import which
        return {"ok": which("codex") is not None, "where": which("codex")}

    def spawn(self, task: dict, bundle_path: str) -> str:
        sid = task["id"]
        transcript = Path.home() / ".aiko" / "transcripts" / f"{sid}.codex.local.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        prompt = task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text()
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
               prompt]
        proc = subprocess.Popen(cmd, stdout=transcript.open("w"), stderr=subprocess.STDOUT)
        self._proc = proc
        return sid

    def status(self, sid: str) -> dict:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            return {"alive": True, "pid": proc.pid}
        return {"alive": False}

    def send_input(self, sid: str, text: str) -> None:
        raise NotImplementedError("local codex one-shot only")

    def kill(self, sid: str) -> None:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            proc.terminate()


class LocalAGYAdapter:
    manifest = LocalAdapterManifest(
        provider_id="antigravity",
        models=["gemini-3-pro", "gemini-3-flash"],
        concurrency_limit=2,
    )

    def health(self) -> dict:
        from shutil import which
        return {"ok": which("agy") is not None, "where": which("agy")}

    def spawn(self, task: dict, bundle_path: str) -> str:
        sid = task["id"]
        transcript = Path.home() / ".aiko" / "transcripts" / f"{sid}.agy.local.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        prompt = task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text()
        # AGY: prompt attaches to the flag (verified Phase 0)
        cmd = ["agy", "--output-format", "text", f"--print={prompt}"]
        proc = subprocess.Popen(cmd, stdout=transcript.open("w"), stderr=subprocess.STDOUT)
        self._proc = proc
        return sid

    def status(self, sid: str) -> dict:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            return {"alive": True, "pid": proc.pid}
        return {"alive": False}

    def send_input(self, sid: str, text: str) -> None:
        raise NotImplementedError("local agy one-shot only")

    def kill(self, sid: str) -> None:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            proc.terminate()


LOCAL_ADAPTERS = {
    "hermes": LocalHermesAdapter(),
    "codex": LocalCodexAdapter(),
    "antigravity": LocalAGYAdapter(),
}
