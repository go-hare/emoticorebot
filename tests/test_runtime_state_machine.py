from __future__ import annotations

import asyncio

from emoticorebot.runtime.session_runtime import SessionRuntime


async def _needs_user_input(task, runtime: SessionRuntime) -> dict[str, object]:
    answer = await runtime.request_input(task, "details", f"请补充 {task.task_id}")
    return {
        "control_state": "completed",
        "status": "success",
        "message": f"{task.task_id}:{answer}",
    }


async def _completes_immediately(task, runtime: SessionRuntime) -> dict[str, object]:
    return {
        "control_state": "completed",
        "status": "success",
        "message": f"{task.task_id}:done",
    }


async def _exercise_waiting_task_promotion() -> None:
    runtime = SessionRuntime(session_id="sess_1", thread_id="thread_1")

    task1 = await runtime.create_task(
        task_id="task_1",
        worker=_needs_user_input,
        params={"channel": "cli", "chat_id": "direct", "origin_message_id": "msg_1"},
        title="first",
    )
    task2 = await runtime.create_task(
        task_id="task_2",
        worker=_needs_user_input,
        params={"channel": "cli", "chat_id": "direct", "origin_message_id": "msg_2"},
        title="second",
    )

    await asyncio.sleep(0)

    assert runtime.waiting_task() is task1
    assert runtime.blocked_task() is task2
    assert task1.status == "waiting_input"
    assert task2.status == "blocked_input"

    assert await runtime.answer("alpha", "task_1", origin_message_id="msg_1b") is True
    await asyncio.sleep(0)

    assert runtime.waiting_task() is task2
    assert runtime.blocked_task() is None
    assert task2.status == "waiting_input"

    assert await runtime.answer("beta", "task_2", origin_message_id="msg_2b") is True

    await asyncio.wait_for(task1.runner, timeout=1.0)
    await asyncio.wait_for(task2.runner, timeout=1.0)

    assert runtime.waiting_task() is None
    assert runtime.blocked_task() is None
    assert runtime.get_task("task_1") is None
    assert runtime.get_task("task_2") is None

    events = []
    while not runtime.to_main_queue.empty():
        events.append(await runtime.to_main_queue.get())

    event_types = [event.get("type") for event in events]
    assert event_types.count("need_input") == 2
    assert event_types.count("done") == 2
    assert [event["task_id"] for event in events if event.get("type") == "need_input"] == ["task_1", "task_2"]


def test_waiting_task_promotion_after_first_answer() -> None:
    asyncio.run(_exercise_waiting_task_promotion())


async def _exercise_cancelling_waiting_task() -> None:
    runtime = SessionRuntime(session_id="sess_2", thread_id="thread_2")

    task1 = await runtime.create_task(task_id="task_a", worker=_needs_user_input, title="first")
    task2 = await runtime.create_task(task_id="task_b", worker=_needs_user_input, title="second")

    await asyncio.sleep(0)

    assert runtime.waiting_task() is task1
    assert runtime.blocked_task() is task2

    await runtime.fail_task(task1, reason="user_cancelled")
    await asyncio.sleep(0)

    assert runtime.waiting_task() is task2
    assert task2.status == "waiting_input"
    assert runtime.get_task("task_a") is None

    if task1.runner is not None:
        try:
            await asyncio.wait_for(task1.runner, timeout=1.0)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("task_a runner should be cancelled after fail_task")

    assert await runtime.answer("gamma", "task_b") is True
    await asyncio.wait_for(task2.runner, timeout=1.0)


def test_cancelling_waiting_task_promotes_next_blocked_task() -> None:
    asyncio.run(_exercise_cancelling_waiting_task())


async def _exercise_terminal_snapshot_retention() -> None:
    runtime = SessionRuntime(session_id="sess_3", thread_id="thread_3")

    task = await runtime.create_task(
        task_id="task_done",
        worker=_completes_immediately,
        params={"channel": "cli", "chat_id": "direct", "origin_message_id": "msg_3"},
        title="done task",
    )
    await asyncio.wait_for(task.runner, timeout=1.0)

    assert runtime.get_task("task_done") is None

    snapshot = runtime.get_task_snapshot("task_done")
    assert snapshot is not None
    assert snapshot["task_id"] == "task_done"
    assert snapshot["status"] == "done"
    assert snapshot["result_status"] == "success"
    assert snapshot["summary"] == "task_done:done"


def test_terminal_task_snapshot_is_retained_after_cleanup() -> None:
    asyncio.run(_exercise_terminal_snapshot_retention())
