"""Tests never use the developer's credentials, ledgers, or live session state."""
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_user_state(tmp_path, monkeypatch):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_dir))
    for key, filename in {
        "AIKO_USAGE_LEDGER": "client-usage.jsonl",
        "AIKOD_USAGE_LEDGER": "server-usage.jsonl",
        "AIKO_FEEDBACK_LEDGER": "feedback.jsonl",
        "AIKO_USAGE_CONFIG": "client-config.yaml",
        "AIKOD_USAGE_CONFIG": "server-config.yaml",
    }.items():
        monkeypatch.setenv(key, str(user_dir / filename))
    import aiko.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", user_dir / ".aiko")
    monkeypatch.setattr(config, "CONFIG_PATH", user_dir / ".aiko/config.yaml")
