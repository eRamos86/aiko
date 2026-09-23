"""AGY adapter: Mac-resident via the agy-bridge HTTP daemon (locality=client).

The bridge runs on the Mac (~/agy-bridge), reachable over Tailscale.
E7 (headless auth) not yet run — until then AGY stays Mac-resident (D12).
"""
import os
from pathlib import Path

import requests

from .base import AdapterManifest, ModelCapability

AGY_BRIDGE_URL = os.environ.get("AGY_BRIDGE_URL", "http://100.102.32.13:4091")
AGY_BRIDGE_TOKEN = os.environ.get("AGY_BRIDGE_TOKEN", "")

MANIFEST = AdapterManifest(
    provider_id="antigravity",
    one_shot=True, resume=True, fork=False, structured_output=True,
    control_daemon="native", concurrency_limit=2,
    quota_model="estimated",
    sandbox_tiers=["workspace-write", "read-only", "plan"],
    health_signals=["exit_code", "stderr_patterns"],
    models=[
        ModelCapability("gemini-3-pro", "antigravity", 0.001, 1000,
                        {"research": 0.9, "plan": 0.95, "implement": 0.7, "debug": 0.75, "review": 0.8, "write_docs": 0.85}),
        ModelCapability("gemini-3-flash", "antigravity", 0.0001, 5000,
                        {"research": 0.6, "plan": 0.7, "implement": 0.5, "debug": 0.6, "review": 0.55, "write_docs": 0.7}),
    ],
    # locality=client: the binary lives on the Mac.
    locality="client",
)


class AGYAdapter:
    manifest = MANIFEST

    def _headers(self):
        return {"Authorization": f"Bearer {AGY_BRIDGE_TOKEN}"}

    def health(self) -> dict:
        try:
            r = requests.get(f"{AGY_BRIDGE_URL}/health", headers=self._headers(), timeout=3)
            return {"ok": r.ok, **r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def spawn(self, task: dict, bundle_path: str) -> str:
        spec = task["spec"] + "\n\n# Context bundle\n" + Path(bundle_path).read_text()
        r = requests.post(
            f"{AGY_BRIDGE_URL}/spawn",
            json={"task_id": task["id"], "spec": spec, "model": task.get("model", "gemini-3-pro")},
            headers=self._headers(),
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["session_id"]

    def status(self, session_id: str) -> dict:
        try:
            r = requests.get(f"{AGY_BRIDGE_URL}/status/{session_id}", headers=self._headers(), timeout=3)
            return r.json() if r.ok else {"alive": False}
        except Exception:
            return {"alive": False}

    def send_input(self, session_id: str, text: str) -> None:
        requests.post(f"{AGY_BRIDGE_URL}/send/{session_id}",
                     json={"text": text}, headers=self._headers(), timeout=3)

    def kill(self, session_id: str) -> None:
        requests.post(f"{AGY_BRIDGE_URL}/kill/{session_id}",
                      headers=self._headers(), timeout=3)
