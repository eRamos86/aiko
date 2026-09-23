"""Writes per-decision log lines to ~/.aiko/routing.log AND inserts into aikod.db."""
import json
import time
from pathlib import Path

LOG_PATH = Path.home() / ".aiko" / "routing.log"


def write_decision(task_id: str, decision) -> None:
    """decision: RoutingDecision dataclass from router.py (duck-typed to avoid circular import)."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task_id": task_id,
        "chosen": {"provider": decision.provider_id, "model": decision.model},
        "yaml_score": decision.yaml_score,
        "observed_modifier": decision.observed_modifier,
        "final_score": decision.final_score,
        "reasoning": decision.reasoning,
        "candidates": decision.candidates,
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")
