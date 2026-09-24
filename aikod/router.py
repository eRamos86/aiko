"""Hybrid router (D3 + D4).

Inputs:
  - ~/.aiko/router.yaml  (explicit floor; user-maintained; per-provider+model scores)
  - ~/.aiko/observed.json (rolling observed-signal modifier)

Output:
  - chosen provider+model + reasoning string

Algorithm:
  1. Score every (provider, model) pair with the YAML floor + observed modifier.
  2. Filter by constraints (daily_quota remaining, locality match, health ok).
  3. Pick the top scorer. Record the decision with full reasoning.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from .adapters.registry import ADAPTERS
from .routing_log import write_decision

ROUTER_YAML_PATH = Path.home() / ".aiko" / "router.yaml"
OBSERVED_PATH = Path.home() / ".aiko" / "observed.json"
HARD_SKIP = 10.0

DEFAULT_YAML = """# Aiko router floor — explicit preferences. Observed signal in observed.json
# adjusts weights up/down from these baselines.
providers:
  hermes:
    models:
      gpt-5.6-luna:    {research: 0.85, plan: 0.6, implement: 0.5, debug: 0.7, review: 0.75, write_docs: 0.8}
  antigravity:
    models:
      gemini-3-pro:     {research: 0.9,  plan: 0.95, implement: 0.7, debug: 0.75, review: 0.8,  write_docs: 0.85}
      gemini-3-flash:   {research: 0.6,  plan: 0.7,  implement: 0.5, debug: 0.6,  review: 0.55, write_docs: 0.7}
  codex:
    models:
      gpt-5.5:          {research: 0.7,  plan: 0.75, implement: 0.95, debug: 0.85, review: 0.85, write_docs: 0.7}
"""


@dataclass
class RoutingDecision:
    provider_id: str
    model: str
    yaml_score: float
    observed_modifier: float
    final_score: float
    reasoning: str
    candidates: list[dict]   # full scoring table for transparency


def load_yaml_floor(path: Path | None = None) -> dict:
    p = path or ROUTER_YAML_PATH
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_YAML)
    import yaml
    return yaml.safe_load(p.read_text())


def load_observed(path: Path | None = None) -> dict:
    p = path or OBSERVED_PATH
    if not p.exists():
        return {"providers": {}}
    return json.loads(p.read_text())


def score_pair(provider_id: str, model_name: str, task_type: str,
               yaml_floor: dict, observed: dict) -> tuple[float, float]:
    """Returns (yaml_score, observed_modifier)."""
    yaml_scores = (yaml_floor.get("providers", {})
                          .get(provider_id, {})
                          .get("models", {})
                          .get(model_name, {}))
    yaml_score = yaml_scores.get(task_type, 0.5)

    obs = observed.get("providers", {}).get(provider_id, {}).get("models", {}).get(model_name, {})
    obs_modifier = obs.get("modifier", {}).get(task_type, 0.0)  # additive in [-0.3, 0.3]
    return yaml_score, obs_modifier


def choose(task_type: str, task_id: str, locality: str = "auto",
           constraints: dict | None = None, adapters: dict | None = None,
           yaml_path: Path | None = None, observed_path: Path | None = None,
           log: bool = True) -> RoutingDecision:
    """Pick provider+model for a task.

    adapters: injectable for tests; defaults to the registry.
    log: write routing.log + db record (disabled in tests via log=False or patched writer).
    """
    yaml_floor = load_yaml_floor(yaml_path)
    observed = load_observed(observed_path)
    constraints = constraints or {}
    pool = adapters if adapters is not None else ADAPTERS

    candidates = []
    for adapter_id, adapter in pool.items():
        if not adapter.health().get("ok"):
            continue
        # Locality filter
        if locality == "local" and adapter.manifest.locality != "client":
            continue
        if locality == "server" and adapter.manifest.locality != "server":
            continue
        for model in adapter.manifest.models:
            # Quota filter
            remaining = constraints.get("daily_remaining", {}).get(
                f"{adapter_id}:{model.name}", model.daily_quota)
            if model.daily_quota is not None and remaining <= 0:
                continue
            yscore, omod = score_pair(adapter_id, model.name, task_type, yaml_floor, observed)
            final = max(0.0, min(1.0, yscore + omod))
            # Usage economy: cooldown/exhausted → skip; heavy use → penalty
            usage_penalty = 0.0
            usage_note = ""
            try:
                from .usage import penalty
                usage_penalty = penalty(adapter_id)
            except Exception:
                pass
            if usage_penalty >= HARD_SKIP:
                continue
            if usage_penalty:
                final = max(0.0, final - usage_penalty)
                usage_note = f" usage=-{usage_penalty}"
            candidates.append({
                "provider": adapter_id, "model": model.name,
                "yaml": yscore, "observed_mod": omod, "final": final,
            })

    if not candidates:
        raise RuntimeError(f"no viable provider+model for task_type={task_type} locality={locality}")

    candidates.sort(key=lambda c: c["final"], reverse=True)
    chosen = candidates[0]
    reasoning = (
        f"task={task_type} locality={locality}. "
        f"Top: {chosen['provider']}/{chosen['model']} (yaml={chosen['yaml']:.2f} "
        f"+ observed={chosen['observed_mod']:+.2f} = {chosen['final']:.2f}). "
        f"Runner-up: " + (f"{candidates[1]['provider']}/{candidates[1]['model']} "
                          f"({candidates[1]['final']:.2f})" if len(candidates) > 1 else "n/a")
    )

    decision = RoutingDecision(
        provider_id=chosen["provider"],
        model=chosen["model"],
        yaml_score=chosen["yaml"],
        observed_modifier=chosen["observed_mod"],
        final_score=chosen["final"],
        reasoning=reasoning,
        candidates=candidates,
    )

    if log:
        write_decision(task_id, decision)
    return decision
