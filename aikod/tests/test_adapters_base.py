from aikod.adapters.base import AdapterManifest, ModelCapability, ProviderAdapter


def test_adapter_manifest_defaults():
    m = AdapterManifest(
        provider_id="test", one_shot=True, resume=False, fork=False,
        structured_output=False, control_daemon="none", concurrency_limit=1,
        quota_model="unmetered", sandbox_tiers=[], health_signals=[],
    )
    assert m.locality == "server"
    assert m.models == []


def test_model_capability_score_default():
    mc = ModelCapability(name="x", provider_id="p", cost_per_1k_tokens=0.0, daily_quota=None)
    assert mc.score("unknown_task") == 0.5  # neutral default
    assert mc.score("plan") == 0.5


def test_runtime_checkable_on_dummy():
    class DummyAdapter:
        manifest = AdapterManifest(
            provider_id="d", one_shot=True, resume=False, fork=False,
            structured_output=False, control_daemon="none", concurrency_limit=1,
            quota_model="unmetered", sandbox_tiers=[], health_signals=[],
        )

        def health(self):
            return {}

        def spawn(self, task, bundle_path):
            return "x"

        def status(self, session_id):
            return {}

        def send_input(self, session_id, text):
            pass

        def kill(self, session_id):
            pass

    assert isinstance(DummyAdapter(), ProviderAdapter)
