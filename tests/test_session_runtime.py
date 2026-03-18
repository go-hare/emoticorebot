from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import (
    ReplyReadyPayload,
    TaskAskPayload,
    TaskEndPayload,
    TaskUpdatePayload,
    TurnInputPayload,
)
from emoticorebot.protocol.task_models import InputRequest, MessageRef, ReplyDraft, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.state_machine import TaskState
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
            state=TaskState.RUNNING,
        )
    )

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=TurnInputPayload(
                input_id="turn_1",
                input_mode="turn",
                session_mode="turn_chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_1"),
                user_text="帮我创建 add.py",
                metadata={"channel_kind": "chat"},
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
            event_type=EventType.OUTPUT_INLINE_READY,
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
    assert snapshot.trace_cursor["task_1"] == ""

    unread = await session.consume_task_trace_summary("sess_1", "task_1", limit=3)
    assert unread == ["正在写 add.py", "文件要放在哪里？", "add.py 已创建"]
    assert session.snapshot("sess_1").trace_cursor["task_1"] == "trace_end_1"
    assert await session.consume_task_trace_summary("sess_1", "task_1", limit=3) == []


def test_session_runtime_tracks_task_views() -> None:
    asyncio.run(_exercise_session_runtime_tracks_task_views())


async def _exercise_session_runtime_tracks_stable_input_fields() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_2",
            turn_id="turn_2",
            correlation_id="turn_2",
            payload=TurnInputPayload(
                input_id="turn_2",
                input_mode="turn",
                session_mode="turn_chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_2"),
                user_text="继续",
                metadata={"channel_kind": "chat"},
            ),
        )
    )
    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_STREAM_CLOSE,
            source="guard",
            target="broadcast",
            session_id="sess_2",
            turn_id="turn_2",
            correlation_id="turn_2",
            payload=ReplyReadyPayload(
                reply=ReplyDraft(
                    reply_id="reply_2",
                    kind="answer",
                    plain_text="收到",
                    metadata={"stream_id": "stream_turn_2", "stream_state": "close"},
                )
            ),
        )
    )
    await bus.drain()

    snapshot = session.snapshot("sess_2")
    assert snapshot.channel_kind == "chat"
    assert snapshot.last_front_instance_id == "front_turn_2"
    assert snapshot.session_summary == "收到"
    assert snapshot.active_reply_stream_id is None
    assert snapshot.archived is True


def test_session_runtime_tracks_stable_input_fields() -> None:
    asyncio.run(_exercise_session_runtime_tracks_stable_input_fields())


async def _exercise_session_runtime_supersedes_active_reply_on_new_stable_input() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_STREAM_OPEN,
            source="guard",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=ReplyReadyPayload(
                reply=ReplyDraft(
                    reply_id="reply_stream_open",
                    kind="answer",
                    plain_text="你",
                    metadata={"stream_id": "stream_turn_stream", "stream_state": "open"},
                )
            ),
        )
    )
    await bus.drain()
    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_next",
            correlation_id="turn_next",
            payload=TurnInputPayload(
                input_id="turn_next",
                input_mode="turn",
                session_mode="turn_chat",
                barge_in=True,
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_next"),
                user_text="我插一句",
                metadata={"channel_kind": "chat"},
            ),
        )
    )
    await bus.drain()

    snapshot = session.snapshot("sess_stream")
    assert snapshot.active_reply_stream_id is None
    assert snapshot.last_user_input == "我插一句"
    assert snapshot.last_front_instance_id == "front_turn_next"


def test_session_runtime_supersedes_active_reply_on_new_stable_input() -> None:
    asyncio.run(_exercise_session_runtime_supersedes_active_reply_on_new_stable_input())


async def _exercise_session_runtime_emits_task_front_trigger() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    session = SessionRuntime(bus=bus, task_store=store)
    session.register()
    triggers: list[BusEnvelope[TurnInputPayload]] = []

    async def _capture(event: BusEnvelope[TurnInputPayload]) -> None:
        if isinstance(event.payload.metadata, dict) and event.payload.metadata.get("front_origin") == "task":
            triggers.append(event)

    bus.subscribe(consumer="test", event_type=EventType.INPUT_TURN_RECEIVED, handler=_capture)

    task = store.add(
        RuntimeTaskRecord(
            task_id="task_2",
            session_id="sess_3",
            turn_id="turn_3",
            request=TaskRequestSpec(request="继续任务", title="继续任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_3"),
            title="继续任务",
            state=TaskState.RUNNING,
        )
    )

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id="sess_3",
            turn_id="turn_3",
            correlation_id="turn_3",
            payload=TurnInputPayload(
                input_id="turn_3",
                input_mode="turn",
                session_mode="turn_chat",
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_3"),
                user_text="帮我继续",
                metadata={"channel_kind": "chat"},
            ),
        )
    )
    task.touch()
    await bus.publish(
        build_envelope(
            event_type=EventType.TASK_ASK,
            source="runtime",
            target="broadcast",
            session_id="sess_3",
            turn_id="turn_3",
            task_id="task_2",
            correlation_id="task_2",
            payload=TaskAskPayload(
                task_id="task_2",
                updated_at=task.updated_at,
                question="还要继续吗？",
                field="confirm",
            ),
        )
    )
    await bus.drain()

    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.payload.metadata["front_origin"] == "task"
    assert trigger.payload.metadata["task_event_type"] == EventType.TASK_ASK
    assert trigger.payload.metadata["task_id"] == "task_2"
    assert str(trigger.turn_id or "").startswith("turn_task_task_2_")
    assert trigger.payload.message.message_id == "msg_3"


def test_session_runtime_emits_task_front_trigger() -> None:
    asyncio.run(_exercise_session_runtime_emits_task_front_trigger())
