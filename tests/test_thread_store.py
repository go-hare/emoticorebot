from __future__ import annotations

from emoticorebot.session.thread_store import ThreadStore


def test_thread_store_persists_brain_and_executor_history(tmp_path) -> None:
    store = ThreadStore(tmp_path)

    thread = store.get_or_create("cli:direct")
    thread.add_message("user", [{"type": "text", "text": "hello"}], message_id="msg_user")
    thread.add_message(
        "assistant",
        [{"type": "text", "text": "hi"}],
        message_id="msg_assistant",
        task={"task_id": "task_1", "status": "done"},
    )
    store.save(thread)
    store.append_executor_messages(
        "cli:direct",
        [{"role": "assistant", "content": "executor note", "message_id": "inner_1", "event_type": "progress"}],
    )

    store.invalidate("cli:direct")
    reloaded = store.get("cli:direct")
    executor = store.get_executor_messages("cli:direct")

    assert reloaded is not None
    assert reloaded.thread_id == "cli:direct"
    assert len(reloaded.messages) == 2
    assert len(reloaded.get_history()) == 2
    assert executor[0]["message_id"] == "inner_1"
    assert executor[0]["event_type"] == "progress"
    assert (tmp_path / "session" / "cli_direct" / "brain.jsonl").exists()
    assert (tmp_path / "session" / "cli_direct" / "executor.jsonl").exists()


def test_thread_store_lists_threads_by_updated_time(tmp_path) -> None:
    store = ThreadStore(tmp_path)

    first = store.get_or_create("cli:first")
    first.add_message("user", [{"type": "text", "text": "one"}], message_id="m1")
    store.save(first)

    second = store.get_or_create("cli:second")
    second.add_message("user", [{"type": "text", "text": "two"}], message_id="m2")
    store.save(second)

    listing = store.list_threads()

    assert {item["thread_id"] for item in listing} == {"cli_first", "cli_second"}
    assert all(item["path"].endswith("brain.jsonl") for item in listing)
