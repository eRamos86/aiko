"""Progressive context bundles (ADR-014): a haiku must not load the vault."""
from pathlib import Path

import pytest


@pytest.fixture
def fake_vault(monkeypatch, tmp_path):
    """Point the daemon's fetch_doc at a fake vault with real doc sizes."""
    import aikod.context as ctx
    docs = {
        "Agents/Context Router.md": "# Context Router\nroutes to everything, ~2KB-ish\n" * 20,
        "Agents/Agents.md": "# Agents — Universal Layer\nHUB " * 200,
        "Agents/Shared-Context.md": "# Shared-Context\nUSER FACTS " * 200,
        "Agents/System/Agent Operating System.md": "# OS\nRULES " * 200,
    }
    def fake_fetch(path):
        return docs.get(path)
    monkeypatch.setattr(ctx, "fetch_doc", fake_fetch)
    return docs


def test_trivial_task_gets_router_only(fake_vault):
    from aikod.context import assemble_bundle
    bundle = assemble_bundle("Write a single haiku about server rooms.")
    assert "Context Router" in bundle
    assert "HUB" not in bundle            # universal hub NOT loaded
    assert "USER FACTS" not in bundle     # shared context NOT loaded
    assert "RULES" not in bundle          # operating system NOT loaded
    assert len(bundle) < 4000             # trivial prompt → tiny bundle


def test_explicit_hints_still_attach(fake_vault):
    from aikod.context import assemble_bundle
    bundle = assemble_bundle("Fix the login bug.",
                             hints=["Platform/Flux/Flux.md"]) \
        if "Platform/Flux/Flux.md" in fake_vault else \
        assemble_bundle("Fix the login bug.",
                        hints=["Agents/Agents.md"])
    assert "HUB" in bundle                # hinted docs ride along


def test_bundle_always_has_task_spec(fake_vault):
    from aikod.context import assemble_bundle
    bundle = assemble_bundle("Do the thing, nya.")
    assert bundle.startswith("# Task\nDo the thing, nya.")
