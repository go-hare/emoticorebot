from __future__ import annotations

import asyncio

from emoticorebot.runtime.event_loop import TaskEventLoop
from emoticorebot.runtime.manager import RuntimeManager
from emoticorebot.runtime.session_runtime import SessionRuntime
from emoticorebot.session.thread_store import ThreadStore


class _DispatcherStub:
    def __init__(self) -> None:
        self.messages = []

    async def publish(self, message) -> None:
        self.messages.append(message)


class _NarratorStub:
    async def handle_task_event(self, **kwargs):
        return {
            "final_message": "任务事件已送达",
            "execution_summary": "已处理任务事件",
            "final_decision": "answer",
            "task_action": "none",
        }


class _EmotionManagerStub:
    def __init__(self) -> None:
        self.pad = type("Pad", (), {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5})()

    def get_emotion_label(self) -> str:
        return "平静"


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


async def _exercise_task_event_serialization_and_cleanup(tmp_path) -> None:
    store = ThreadStore(tmp_path)
    thread = store.get_or_create("sess_1")
    thread.add_message("user", [{"type": "text", "text": "hello"}], message_id="msg_user")
    store.save(thread)

    runtime = SessionRuntime(session_id="sess_1", thread_id="sess_1")
    manager = RuntimeManager(lambda _session_id: runtime)
    manager.get_or_create_runtime("sess_1")

    dispatcher = _DispatcherStub()
    locks: dict[str, asyncio.Lock] = {}

    def _session_lock_for(session_id: str) -> asyncio.Lock:
        return locks.setdefault(session_id, asyncio.Lock())

    event_loop = TaskEventLoop(
        runtime_manager=manager,
        thread_store=store,
        dispatcher=dispatcher,
        event_narrator=_NarratorStub(),
        emotion_mgr=_EmotionManagerStub(),
        memory_window=20,
        new_message_id=lambda: "msg_task_event",
        schedule_turn_reflection=lambda **kwargs: None,
        session_lock_for=_session_lock_for,
    )
    event_loop.ensure_consumer("sess_1", runtime)

    session_lock = _session_lock_for("sess_1")
    await session_lock.acquire()
    await runtime.to_main_queue.put(
        {
            "task_id": "task_1",
            "type": "done",
            "summary": "完成",
            "channel": "cli",
            "chat_id": "direct",
            "message_id": "msg_origin",
            "params": {"task_id": "task_1", "title": "任务一"},
        }
    )

    await asyncio.sleep(0.05)
    assert dispatcher.messages == []

    session_lock.release()

    await _wait_for(lambda: len(dispatcher.messages) == 1)
    assert dispatcher.messages[0].content == "任务事件已送达"

    reloaded = store.get("sess_1")
    assert reloaded is not None
    assert len(reloaded.messages) == 2
    assert reloaded.messages[-1]["role"] == "assistant"

    await _wait_for(lambda: manager.get("sess_1") is None)
    event_loop.stop()


def test_task_event_waits_for_session_lock_and_cleans_up_idle_runtime(tmp_path) -> None:
    asyncio.run(_exercise_task_event_serialization_and_cleanup(tmp_path))


async def _exercise_explicit_idle_release(tmp_path) -> None:
    store = ThreadStore(tmp_path)
    runtime = SessionRuntime(session_id="sess_idle", thread_id="sess_idle")
    manager = RuntimeManager(lambda _session_id: runtime)
    manager.get_or_create_runtime("sess_idle")

    locks: dict[str, asyncio.Lock] = {}

    def _session_lock_for(session_id: str) -> asyncio.Lock:
        return locks.setdefault(session_id, asyncio.Lock())

    event_loop = TaskEventLoop(
        runtime_manager=manager,
        thread_store=store,
        dispatcher=_DispatcherStub(),
        event_narrator=_NarratorStub(),
        emotion_mgr=_EmotionManagerStub(),
        memory_window=20,
        new_message_id=lambda: "msg_idle",
        schedule_turn_reflection=lambda **kwargs: None,
        session_lock_for=_session_lock_for,
    )
    event_loop.ensure_consumer("sess_idle", runtime)

    await asyncio.sleep(0)
    event_loop.release_session("sess_idle", runtime=runtime)
    await asyncio.sleep(0)

    assert manager.get("sess_idle") is None
    event_loop.stop()


def test_release_session_removes_idle_runtime_without_events(tmp_path) -> None:
    asyncio.run(_exercise_explicit_idle_release(tmp_path))
