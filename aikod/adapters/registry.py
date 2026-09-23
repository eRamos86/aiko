"""Adapter registry: the set of adapters aikod knows about + health aggregation."""
from .hermes import HermesAdapter
from .codex import CodexAdapter
from .antigravity import AGYAdapter

ADAPTERS: dict = {
    "hermes": HermesAdapter(),
    "codex": CodexAdapter(),
    "antigravity": AGYAdapter(),
}


def healthy_adapters() -> dict:
    return {k: v for k, v in ADAPTERS.items() if v.health().get("ok")}
