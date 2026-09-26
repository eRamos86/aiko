"""Model catalog + priors + exploration (ADR-017)."""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AIKO_FEEDBACK_LEDGER", str(tmp_path / "fb.jsonl"))
    import aiko.models_catalog as mc
    monkeypatch.setattr(mc, "CACHE_PATH", tmp_path / "catalog.json")
    return mc


def test_priors_reasoning_models_boost_plan(env):
    mc = env
    p = mc.priors_for("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
    assert p["plan"] > 0.5 and p["research"] > 0.5
    p2 = mc.priors_for("some-plain-instruct-model")
    assert p2["plan"] == 0.5


def test_priors_coder_models_boost_code(env):
    mc = env
    p = mc.priors_for("qwen3-coder-480b")
    assert p["code"] > 0.5


def test_priors_small_models_boost_chat(env):
    mc = env
    p = mc.priors_for("nemotron-nano-30b")
    assert p["chat"] > 0.5


def test_catalog_discovery_caches(env, monkeypatch):
    mc = env
    calls = []
    def fake_fetch(key, brain):
        calls.append(key)
        return ["m1", "m2", "m2"]
    monkeypatch.setattr(mc, "_provider_brains",
                        lambda: {"nim": {"base_url": "x", "api_key": "y"}})
    monkeypatch.setattr(mc, "_fetch_provider", fake_fetch)
    c1 = mc.catalog()
    c2 = mc.catalog()   # second call → cache, no refetch
    assert c1 == {"nim": ["m1", "m2"]}
    assert len(calls) == 1


def test_rank_models_applies_learning(env, monkeypatch):
    mc = env
    monkeypatch.setattr(mc, "catalog",
                        lambda: {"nim": ["m-reasoning", "m-plain", "m-coder"]})
    from aiko.feedback import record_outcome
    # m-coder proves itself at implement (3 wins)
    for _ in range(3):
        record_outcome("self", "implement", True, model="m-coder")
    rows = mc.rank_models("implement", limit=10)
    top = rows[0]
    assert top["model"] == "m-coder"
    assert top["feedback"] > 0
    assert "learned" in top["why"]


def test_pick_brain_explores_low_stakes(env, monkeypatch):
    import aiko.selection as sel
    import aiko.models_catalog as mc
    monkeypatch.setattr(mc, "catalog",
                        lambda: {"nim": ["m-a", "m-b", "m-c"]})
    picks = set()
    for _ in range(60):   # ε=0.15 → over 60 low-stakes picks, samples appear
        pick = sel.pick_brain("trivial", explore=True)
        assert pick is not None
        picks.add(pick["model"])
    assert len(picks) >= 2   # exploration actually explores, nya


def test_pick_brain_heavy_shape_sticks_to_top(env, monkeypatch):
    import aiko.selection as sel
    import aiko.models_catalog as mc
    monkeypatch.setattr(mc, "catalog",
                        lambda: {"nim": ["m-a", "m-b", "m-c"]})
    seen = set()
    for _ in range(20):
        pick = sel.pick_brain("research", explore=True)   # not low-stakes
        assert pick is not None
        seen.add(pick["model"])
    assert seen == {"m-a"}    # never explores away from the top pick


def test_brain_from_provider_model_builds_adhoc():
    from aiko.brain import Brain
    b = Brain.from_provider_model("nim", "nvidia/test-model")
    assert b.model == "nvidia/test-model"
    assert "nvidia" in b.base_url or b.base_url
