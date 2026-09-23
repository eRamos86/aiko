import json
from aikod.events import append, get_for_session
from aikod.db import connect


def test_append_and_read(tmp_path):
    c = connect(tmp_path / "aikod.db")
    append(c, "s1", "task.created", {"spec": "hello"})
    append(c, "s1", "task.routed", {"provider": "hermes"})
    events = get_for_session(c, "s1")
    assert len(events) == 2
    assert events[0]["seq"] == 1
    assert events[0]["payload"] == {"spec": "hello"}
    assert events[1]["payload"] == {"provider": "hermes"}


def test_sessions_seq_independent(tmp_path):
    c = connect(tmp_path / "aikod.db")
    append(c, "s1", "a", {})
    append(c, "s2", "a", {})  # should get seq 1, not 2
    assert get_for_session(c, "s2")[0]["seq"] == 1
