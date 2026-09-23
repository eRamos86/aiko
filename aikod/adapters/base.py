from typing import Protocol, runtime_checkable, Literal
from dataclasses import dataclass, field

ControlDaemon = Literal["tmux-bridge", "native", "none"]
QuotaModel = Literal["unmetered", "estimated", "metered"]


@dataclass
class ModelCapability:
    """Per-model capability score (0.0–1.0). Used as the YAML floor (D3)."""
    name: str                    # e.g. "gpt-5.5", "gemini-3-pro", "glm-5.2"
    provider_id: str
    cost_per_1k_tokens: float    # USD, for estimated providers
    daily_quota: int | None      # None = unmetered
    scores: dict[str, float] = field(default_factory=dict)
    # scores keys: research, plan, implement, debug, review, write_docs

    def score(self, task_type: str) -> float:
        return self.scores.get(task_type, 0.5)


@dataclass
class AdapterManifest:
    provider_id: str
    one_shot: bool
    resume: bool
    fork: bool
    structured_output: bool
    control_daemon: ControlDaemon
    concurrency_limit: int
    quota_model: QuotaModel
    sandbox_tiers: list[str]
    health_signals: list[str]
    models: list[ModelCapability] = field(default_factory=list)
    # Locality: server adapter lives on poopmachine; client adapter (Phase 4) lives on host.
    locality: Literal["server", "client"] = "server"


@runtime_checkable
class ProviderAdapter(Protocol):
    manifest: AdapterManifest

    def health(self) -> dict: ...
    def spawn(self, task: dict, bundle_path: str) -> str: ...   # returns session_id
    def status(self, session_id: str) -> dict: ...
    def send_input(self, session_id: str, text: str) -> None: ...
    def kill(self, session_id: str) -> None: ...
