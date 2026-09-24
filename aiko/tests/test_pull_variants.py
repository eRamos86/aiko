"""Size-aware ollama pull variants."""
import pytest

from aiko import models_catalog as mc


@pytest.fixture(autouse=True)
def fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "CACHE_PATH", tmp_path / "cat.json")
    monkeypatch.setattr(mc, "_CACHE_MEMORY", None)
    yield
    monkeypatch.setattr(mc, "_CACHE_MEMORY", None)


def test_param_regex():
    m = mc._PARAM_RE.match("deepseek-r1:14b")
    assert m and m.group(2) == "14" and m.group(3).lower() == "b"
    assert mc._PARAM_RE.match("qwen3:0.6b").group(2) == "0.6"
    assert mc._PARAM_RE.match("mistral:7b-instruct-q4_K_M")  # quant suffix ok
    assert mc._PARAM_RE.match("nomic-embed-text:latest") is None


def test_gb_needed_reasonable():
    assert mc._gb_needed(7) == 4.4      # 7B @ q4 ≈ 4.4 GB
    assert mc._gb_needed(70) == 44.3    # 70B @ q4 ≈ 44 GB
    assert mc._gb_needed(0.6) == 0.4


def test_registry_pull_models_includes_local_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "_fetch_ollama_installable",
                        lambda local: ["deepseek-r1", "qwen3"])
    monkeypatch.setattr(mc, "catalog0_names",
                        lambda: ["deepseek-r1:32b", "qwen3:32b"])
    monkeypatch.setattr(mc, "_free_ram_gb", lambda: 24.0)

    class R:
        text = ('<a href="/library/deepseek-r1:7b">7b</a>'
                '<a href="/library/deepseek-r1:32b">32b</a>'
                '<a href="/library/deepseek-r1:70b">70b</a>'
                '<a href="/library/qwen3:0.6b">0.6b</a>'
                '<a href="/library/qwen3:30b">30b</a>')
        def raise_for_status(self):
            return None

    monkeypatch.setattr(__import__("httpx"), "get", lambda *a, **k: R())
    rows = {r["model"]: r for r in mc.registry_pull_models(force=True)}
    # 7b fits (~4.4GB) and beats excluded 32b; 70b flagged too-big
    assert "deepseek-r1:7b" in rows and rows["deepseek-r1:7b"]["fits"]
    assert "deepseek-r1:32b" not in rows            # installed locally
    assert "deepseek-r1:70b" in rows and not rows["deepseek-r1:70b"]["fits"]
    assert "qwen3:0.6b" in rows                     # tiniest tag surfaced
