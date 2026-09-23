"""Local adapter manifest — lighter than the server's (D2 local path)."""
from dataclasses import dataclass, field


@dataclass
class LocalAdapterManifest:
    provider_id: str
    models: list[str] = field(default_factory=list)
    concurrency_limit: int = 1
    locality: str = "client"
