import json
import pytest
from pathlib import Path

from aikod.router import choose, load_yaml_floor, load_observed, DEFAULT_YAML
from aikod.adapters.base import AdapterManifest, ModelCapability


class FakeAdapter:
    """Minimal in-memory adapter for router tests."""

    def __init__(self, provider_id, locality, models, healthy=True):
        self.manifest = AdapterManifest(
            provider_id=provider_id, one_shot=True, resume=False, fork=False,
            structured_output=False, control_daemon="none", concurrency_limit=1,
            quota_model="unmetered", sandbox_tiers=[], health_signals=[],
            models=models, locality=locality,
        )
        self._healthy = healthy
        self.health_calls = 0

    def health(self):
        self.health_calls += 1
        return {"ok": self._healthy}

    def spawn(self, task, bundle_path):
        return task["id"]

    def status(self, session_id):
        return {"alive": False}

    def send_input(self, session_id, text):
        pass

    def kill(self, session_id):
        pass


def hermes_like():
    return FakeAdapter("hermes", "server", [
        ModelCapability("gpt-5.6-luna", "hermes", 0, None, {}),
    ])


def agy_like():
    return FakeAdapter("antigravity", "client", [
        ModelCapability("gemini-3-pro", "antigravity", 0.001, 1000, {}),
        ModelCapability("gemini-3-flash", "antigravity", 0.0001, 5000, {}),
    ])


def codex_like():
    return FakeAdapter("codex", "server", [
        ModelCapability("gpt-5.5", "codex", 0.005, 500, {}),
    ])


@pytest.fixture
def fake_adapters():
    return {"hermes": hermes_like(), "antigravity": agy_like(), "codex": codex_like()}


@pytest.fixture
def clean(tmp_path):
    return {"yaml": tmp_path / "router.yaml", "obs": tmp_path / "observed.json"}


def test_default_yaml_written_on_first_run(clean):
    load_yaml_floor(clean["yaml"])
    assert clean["yaml"].exists()
    assert "providers" in clean["yaml"].read_text()


def test_choose_prefers_yaml_top_scorer(fake_adapters, clean):
    d = choose("plan", "task-1", locality="server",
              adapters=fake_adapters, yaml_path=clean["yaml"],
              observed_path=clean["obs"], log=False)
    # YAML floor: gemini-3-pro plan=0.95 but it's client-locality; for server
    # locality, gpt-5.5 (0.75) beats glm-5.2 (0.7) and nemotron (0.6).
    assert d.provider_id == "codex"
    assert d.model == "gpt-5.5"


def test_choose_local_considers_client_only(fake_adapters, clean):
    d = choose("plan", "task-1", locality="local",
              adapters=fake_adapters, yaml_path=clean["yaml"],
              observed_path=clean["obs"], log=False)
    assert d.provider_id == "antigravity"
    assert d.model == "gemini-3-pro"


def test_observed_modifier_can_override_floor(fake_adapters, clean):
    clean["yaml"].write_text(DEFAULT_YAML)
    clean["obs"].write_text(json.dumps({
        "providers": {"hermes": {"models": {"gpt-5.6-luna": {"modifier": {"plan": 0.3}}}}}
    }))
    d = choose("plan", "task-1", locality="server",
              adapters=fake_adapters, yaml_path=clean["yaml"],
              observed_path=clean["obs"], log=False)
    # 0.6 + 0.3 = 0.9 vs gpt-5.5 0.75 — hermes gpt-5.6-luna wins
    assert d.provider_id == "hermes"
    assert d.model == "gpt-5.6-luna"
    assert d.observed_modifier == pytest.approx(0.3)


def test_unhealthy_provider_excluded(fake_adapters, clean):
    fake_adapters["codex"]._healthy = False
    d = choose("implement", "t1", locality="server",
               adapters=fake_adapters, yaml_path=clean["yaml"],
               observed_path=clean["obs"], log=False)
    # codex unhealthy; implement's best remaining server model = hermes gpt-5.6-luna (0.5)
    assert d.provider_id == "hermes"
    assert d.model == "gpt-5.6-luna"


def test_quota_exhaustion_excludes(fake_adapters, clean):
    clean["yaml"].write_text(DEFAULT_YAML)
    d = choose("implement", "t1", locality="server",
               constraints={"daily_remaining": {"codex:gpt-5.5": 0}},
               adapters=fake_adapters, yaml_path=clean["yaml"],
               observed_path=clean["obs"], log=False)
    assert d.provider_id != "codex"


def test_candidates_table_is_full(fake_adapters, clean):
    d = choose("implement", "t1", locality="auto",
               adapters=fake_adapters, yaml_path=clean["yaml"],
               observed_path=clean["obs"], log=False)
    # 1 hermes model + 2 agy + 1 codex = 4 candidates
    assert len(d.candidates) == 4


def test_no_viable_raises(fake_adapters, clean):
    for a in fake_adapters.values():
        a._healthy = False
    with pytest.raises(RuntimeError, match="no viable provider"):
        choose("plan", "t1", adapters=fake_adapters,
               yaml_path=clean["yaml"], observed_path=clean["obs"], log=False)


def test_reasoning_mentions_runner_up(fake_adapters, clean):
    d = choose("plan", "t1", locality="server",
               adapters=fake_adapters, yaml_path=clean["yaml"],
               observed_path=clean["obs"], log=False)
    assert "Runner-up:" in d.reasoning
    assert "yaml=" in d.reasoning
