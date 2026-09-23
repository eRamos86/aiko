import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from aiko.auth import login, load_token, auth_headers
from aiko.config import save_config, load_config


def test_save_and_load_config(tmp_path, monkeypatch):
    monkeypatch.setattr("aiko.config.CONFIG_PATH", tmp_path / "config.yaml")
    save_config({"daemon_url": "https://aikod.eramos.us"})
    save_config({"vault": {"api_url": "x"}})  # merge, not overwrite
    cfg = load_config()
    assert cfg["daemon_url"] == "https://aikod.eramos.us"
    assert cfg["vault"]["api_url"] == "x"


def test_login_stores_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("aiko.auth.CRED_PATH", tmp_path / "credentials.yaml")

    def fake_post(url, json=None, timeout=None):
        m = MagicMock(status_code=200)
        m.raise_for_status.return_value = None
        m.json.return_value = {
            "token": "tok-123",
            "refresh_token": "rt-456",
            "user": {"id": 1, "email": "ethan@eramos.us", "name": "Ethan"},
        }
        return m

    with patch("aiko.auth.httpx.post", side_effect=fake_post):
        user = login("ethan@eramos.us", "pw")

    assert user["email"] == "ethan@eramos.us"
    creds = yaml.safe_load((tmp_path / "credentials.yaml").read_text())
    assert creds["token"] == "tok-123"
    assert creds["refresh_token"] == "rt-456"
    assert (tmp_path / "credentials.yaml").stat().st_mode & 0o777 == 0o600


def test_auth_headers_requires_login(tmp_path, monkeypatch):
    monkeypatch.setattr("aiko.auth.CRED_PATH", tmp_path / "credentials.yaml")
    import pytest
    with pytest.raises(SystemExit):
        auth_headers()


def test_auth_headers_returns_bearer(tmp_path, monkeypatch):
    monkeypatch.setattr("aiko.auth.CRED_PATH", tmp_path / "credentials.yaml")
    (tmp_path / "credentials.yaml").write_text(yaml.safe_dump({"token": "abc"}))
    assert auth_headers() == {"Authorization": "Bearer abc"}
