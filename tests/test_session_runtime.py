from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import (
    ReplyReadyPayload,
    TaskAskPayload,
    TaskEndPayload,
    TaskUpdatePayload,
    UserMessagePayload,
)
from emoticorebot.protocol.task_models import InputRequest, MessageRef, ReplyDraft, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.state_machine import TaskStatus
from emoticorebot.runtime.task_store import RuntimeTaskRecord, TaskStore
from emoticorebot.session.runtime import SessionRuntime


async def _exercise_session_runtime_tracks_task_views() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()

    task = store.add(
        RuntimeTaskRecord(
            task_id="task_1",
            session_id="sess_1",
            turn_id="turn_1",
            request=TaskRequestSpec(request="创建 add.py", title="创建 add.py"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            title="创建 add.py",
            status=TaskStatus.RUNNING,
        )
    )

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_USER_MESSAGE,
            source="gateway",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=UserMessagePayload(
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_1"),
                plain_text="帮我创建 add.py",
            ),
        )
    )
    task.touch()
    await bus.publish(
        build_envelope(
            event_type=EventType.TASK_UPDATE,
            source="runtime",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=TaskUpdatePayload(
                task_id="task_1",
                updated_at=task.updated_at,
                message="正在写 add.py",
                trace_append=[
                    {
                        "trace_id": "trace_update_1",
                        "task_id": "task_1",
                        "session_id": "sess_1",
                        "ts": task.updated_at,
                        "kind": "progress",
                        "message": "正在写 add.py",
                        "data": {"stage": "write"},
                    }
                ],
            ),
        )
    )
    task.touch()
    await bus.publish(
        build_envelope(
            event_type=EventType.TASK_ASK,
            source="runtime",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=TaskAskPayload(
                task_id="task_1",
                updated_at=task.updated_at,
                question="文件要放在哪里？",
                field="path",
                why="需要补充文件位置",
                trace_append=[
                    {
                        "trace_id": "trace_ask_1",
                        "task_id": "task_1",
                        "session_id": "sess_1",
                        "ts": task.updated_at,
                        "kind": "ask",
                        "message": "文件要放在哪里？",
                        "data": {"field": "path"},
                    }
                ],
            ),
        )
    )
    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_REPLY_APPROVED,
            source="guard",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=ReplyReadyPayload(
                reply=ReplyDraft(
                    reply_id="reply_1",
                    kind="ask_user",
                    plain_text="文件要放在哪里？",
                )
            ),
        )
    )
    task.touch()
    await bus.publish(
        build_envelope(
            event_type=EventType.TASK_END,
            source="runtime",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=TaskEndPayload(
                task_id="task_1",
                result="success",
                updated_at=task.updated_at,
                summary="add.py 已创建",
                output="已完成。",
                trace_final=[
                    {
                        "trace_id": "trace_end_1",
                        "task_id": "task_1",
                        "session_id": "sess_1",
                        "ts": task.updated_at,
                        "kind": "summary",
                        "message": "add.py 已创建",
                        "data": {"result": "success"},
                    }
                ],
            ),
        )
    )
    await bus.drain()

    snapshot = session.snapshot("sess_1")
    assert snapshot.last_user_input == "帮我创建 add.py"
    assert snapshot.last_assistant_output == "文件要放在哪里？"
    assert snapshot.active_task_ids == []
    assert snapshot.waiting_task_ids == []
    assert snapshot.done_task_ids == ["task_1"]

    task_view = snapshot.tasks["task_1"]
    assert task_view.state == "done"
    assert task_view.result == "success"
    assert task_view.summary == "add.py 已创建"
    assert task_view.latest_ask == "文件要放在哪里？"
    assert session.task_trace_summary("task_1", limit=3) == ["正在写 add.py", "文件要放在哪里？", "add.py 已创建"]
    assert snapshot.trace_cursor["task_1"] == "trace_end_1"


def test_session_runtime_tracks_task_views() -> None:
    asyncio.run(_exercise_session_runtime_tracks_task_views())
