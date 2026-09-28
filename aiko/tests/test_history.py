from aiko.history import ConversationStore, resumable_history


def test_reopening_restores_and_new_conversation_preserves_old(tmp_path):
    path = tmp_path / "conversations.db"
    store = ConversationStore(path)
    cid, _ = store.latest()
    history = [{"role": "user", "content": "Keep this task"}]
    store.save(cid, history)
    assert ConversationStore(path).latest() == (cid, history)
    store.archive(cid)
    assert store.latest()[0] != cid
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM conversation").fetchone()[0] == 1


def test_interrupted_tool_round_not_replayed_as_complete():
    messages = [{"role": "user", "content": "task"},
                {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
                {"role": "tool", "tool_call_id": "a", "content": "done"}]
    assert resumable_history(messages) == messages[:1]
    messages.append({"role": "tool", "tool_call_id": "b", "content": "done"})
    assert resumable_history(messages) == messages
