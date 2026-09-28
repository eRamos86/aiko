import pytest


@pytest.mark.asyncio
async def test_direct_tab_available_without_brain_and_opens_selected_host(monkeypatch):
    import aiko.tui as tui
    from textual.widgets import Input, Select, TabbedContent
    cfg = {"agents": [{"name": "opencode", "command": "opencode"}],
           "servers": [{"name": "server", "ssh_host": "example"}]}
    monkeypatch.setattr("aiko.config.load_config", lambda: cfg)
    monkeypatch.setattr(tui, "list_brains", lambda: [])
    monkeypatch.setattr(tui, "get_backend", lambda: object())
    calls = []

    class Manager:
        def open(self, agent, cwd, target):
            calls.append((agent, cwd, target))
            return {"id": "s1", "agent": agent, "cwd": cwd, "target": target}

        def list(self):
            return []

    monkeypatch.setattr("aiko.session_manager.SessionManager", Manager)

    class App(tui.AikoTUI):
        def on_mount(self):
            pass

        def _enter_direct_terminal(self, sid, target):
            calls.append((sid, target))

    app = App()
    async with app.run_test(size=(110, 40)) as pilot:
        app.query_one(TabbedContent).active = "agents"
        app.query_one("#direct_agent", Select).value = "opencode"
        app.query_one("#direct_target", Select).value = "server"
        app.query_one("#direct_cwd", Input).value = "/projects/My Flux"
        await app._open_direct_session()
        assert calls == [("opencode", "/projects/My Flux", "server"), ("s1", "server")]
