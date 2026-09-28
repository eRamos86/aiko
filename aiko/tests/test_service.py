import pytest

from aiko import service


def test_restart_rejects_service_that_kills_worker_group(monkeypatch):
    monkeypatch.setattr(service, "service_state", lambda name: {
        "KillMode": "control-group", "Restart": "always", "MainPID": "200"})
    monkeypatch.setattr(service.os, "kill", lambda *args: pytest.fail("must not signal"))
    with pytest.raises(RuntimeError, match="KillMode=process"):
        service.restart()


def test_restart_rejects_inactive_service_without_signalling(monkeypatch):
    monkeypatch.setattr(service, "service_state", lambda name: {
        "KillMode": "process", "Restart": "always", "MainPID": "0"})
    monkeypatch.setattr(service.os, "kill", lambda *args: pytest.fail("must not signal"))
    with pytest.raises(RuntimeError, match="not running"):
        service.restart()
