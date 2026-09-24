"""Usage economy: budgets, ledger math, cooldowns, penalties, harvesting."""
import json
import time

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AIKO_USAGE_LEDGER", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("AIKO_USAGE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("AIKO_USAGE_COOLDOWNS", str(tmp_path / "cooldowns.json"))
    return tmp_path


def _write_cfg(tmp_path, usage=None):
    import yaml
    cfg = {"brains": []}
    if usage:
        cfg["usage"] = usage
    yaml.dump(cfg, (tmp_path / "config.yaml").open("w"))


def test_provider_key_detection():
    from aiko.usage import provider_key
    assert provider_key("https://integrate.api.nvidia.com/v1") == "nim"
    assert provider_key("https://openrouter.ai/api/v1") == "openrouter"
    assert provider_key("https://api.example.com/v1") == "api.example.com"


def test_parse_and_fmt_window():
    from aiko.usage import parse_window, fmt_window
    assert parse_window("60") == 60
    assert parse_window("40m") == 2400
    assert parse_window("5h") == 18000
    assert parse_window("7d") == 604800
    assert fmt_window(60) == "1m"
    assert fmt_window(18000) == "5h"
    assert fmt_window(604800) == "1w"
    with pytest.raises(ValueError):
        parse_window("soon")


def test_record_and_window_use(env):
    from aiko.usage import record, window_use
    record("nim", "m", "requests", 1)
    record("nim", "m", "requests", 1)
    record("nim", "m", "tokens", 500)
    used, _ = window_use("nim", "requests", 60)
    assert used == 2
    used, _ = window_use("nim", "tokens", 60)
    assert used == 500


def test_remaining_respects_window(env):
    from aiko.usage import record, window_use
    # simulate an old event by writing the ledger directly
    p = env / "usage.jsonl"
    p.write_text(json.dumps({"ts": time.time() - 120, "key": "nim",
                             "model": "m", "unit": "requests",
                             "amount": 30, "source": "test"}) + "\n")
    from aiko.usage import record
    record("nim", "m", "requests", 5)
    used, _ = window_use("nim", "requests", 60)   # old one aged out
    assert used == 5


def test_nim_default_budget_and_penalty_curve(env):
    _write_cfg(env)  # no usage section → NIM default 40/min applies
    from aiko.usage import penalty, record, remaining
    rows = remaining("nim")
    assert rows and rows[0]["cap"] == 40
    assert penalty("nim") == 0.0
    for _ in range(20):
        record("nim", "m", "requests", 1)          # 50% used
    assert penalty("nim") == 0.0                    # ≤50% is free
    for _ in range(15):
        record("nim", "m", "requests", 1)           # 87.5% used
    p = penalty("nim")
    assert 0.0 < p < 0.5
    for _ in range(5):
        record("nim", "m", "requests", 1)           # 100% → hard skip
    assert penalty("nim") == 10.0


def test_no_budgets_means_no_penalty_but_counts(env):
    _write_cfg(env)
    from aiko.usage import exhaustion, penalty, record, snapshot_all
    record("codex", "gpt-5.5", "requests", 1)
    assert exhaustion("codex") is None
    assert penalty("codex") == 0.0
    snap = snapshot_all()
    assert snap["codex"]["raw_24h"]["requests"] == 1


def test_429_sets_cooldown_and_hard_skips(env):
    from aiko.usage import in_cooldown, note_429, penalty
    note_429("codex", "gpt-5.5", retry_after="120")
    assert in_cooldown("codex") > 100
    assert penalty("codex") == 10.0


def test_config_budgets_override_defaults(env):
    _write_cfg(env, usage={"codex": {"budgets": [
        {"unit": "requests", "cap": 100, "window": 18000},
        {"unit": "requests", "cap": 500, "window": 604800}]}})
    from aiko.usage import budgets_for, exhaustion
    assert len(budgets_for("codex")) == 2
    assert exhaustion("codex") == 1.0


def test_note_response_harvests_tokens(env):
    from aiko.usage import note_response, window_use
    note_response("nim", "m", usage={"prompt_tokens": 120,
                                     "completion_tokens": 80})
    used, _ = window_use("nim", "requests", 60)
    assert used == 1
    used, _ = window_use("nim", "tokens", 60)
    assert used == 200


def test_set_budget_writes_config(env):
    from aiko.usage import set_budget, budgets_for
    set_budget("codex", "requests", 40, "5h")
    set_budget("codex", "requests", 500, "7d")
    set_budget("codex", "requests", 42, "5h")  # replace same window
    b = budgets_for("codex")
    assert {(x["unit"], x["cap"], x["window"]) for x in b} == {
        ("requests", 42, 18000), ("requests", 500, 604800)}


def test_router_skips_exhausted_provider(env, tmp_path):
    _write_cfg(env, usage={"codex": {"budgets": [
        {"unit": "requests", "cap": 1, "window": 86400}]}})
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("AIKOD_USAGE_LEDGER", str(env / "usage.jsonl"))
    monkeypatch.setenv("AIKOD_USAGE_CONFIG", str(env / "config.yaml"))
    from aikod.usage import record as daemon_record
    daemon_record("codex", "gpt-5.5", "requests", 1)  # exhausted

    import yaml
    router_yaml = tmp_path / "router.yaml"
    router_yaml.write_text(yaml.dump({
        "providers": {
            "hermes": {"models": {"m1": {"implement": 0.6}}},
            "codex": {"models": {"gpt-5.5": {"implement": 0.95}}},
        }}))

    class Model:
        def __init__(self, name, quota):
            self.name, self.daily_quota = name, quota

    class Adapter:
        def __init__(self, locality, models):
            from types import SimpleNamespace
            self.manifest = SimpleNamespace(locality=locality, models=models)

        def health(self):
            return {"ok": True}

    adapters = {
        "hermes": Adapter("server", [Model("m1", None)]),
        "codex": Adapter("server", [Model("gpt-5.5", None)]),
    }
    from aikod.router import choose
    d = choose("implement", "t1", adapters=adapters,
               yaml_path=router_yaml, observed_path=tmp_path / "none.json",
               log=False)
    assert d.provider_id == "hermes"  # codex 0.95 skipped: budget spent
    monkeypatch.undo()
