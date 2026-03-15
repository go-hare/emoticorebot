from __future__ import annotations

import asyncio

from emoticorebot.brain.executive import ExecutiveBrain
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.commands import BrainReplyPayload
from emoticorebot.protocol.events import TaskProgressEventPayload, TaskResultEventPayload
from emoticorebot.protocol.task_models import MessageRef, ProtocolModel, TaskRequestSpec
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.state_machine import TaskStatus
from emoticorebot.runtime.task_store import RuntimeTaskRecord, TaskStore


def _task_store() -> TaskStore:
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_1",
            session_id="sess_1",
            turn_id="turn_1",
            request=TaskRequestSpec(request="完成任务", title="完成任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            title="完成任务",
            status=TaskStatus.DONE,
            summary="done",
        )
    )
    return store


async def _exercise_terminal_reflection() -> None:
    bus = PriorityPubSubBus()
    store = _task_store()
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    turn_events: list[BusEnvelope[ProtocolModel]] = []
    deep_events: list[BusEnvelope[ProtocolModel]] = []

    async def _capture_turn(event: BusEnvelope[ProtocolModel]) -> None:
        turn_events.append(event)

    async def _capture_deep(event: BusEnvelope[ProtocolModel]) -> None:
        deep_events.append(event)

    bus.subscribe(consumer="memory_governor", event_type=EventType.MEMORY_REFLECT_TURN, handler=_capture_turn)
    bus.subscribe(consumer="memory_governor", event_type=EventType.MEMORY_REFLECT_DEEP, handler=_capture_deep)

    task = store.require("task_1")
    await bus.publish(
        build_envelope(
            event_type=EventType.TASK_EVENT_RESULT,
            source="runtime",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=TaskResultEventPayload(
                task_id="task_1",
                state=task.snapshot(),
                summary="已完成",
                result_text="任务已经完成。",
            ),
        )
    )
    await bus.drain()

    assert len(turn_events) == 1
    assert turn_events[0].payload.reason == "task_result"
    assert "reflection_input" in turn_events[0].payload.metadata
    assert len(deep_events) == 1
    assert deep_events[0].payload.reason == "task_result"
    assert deep_events[0].payload.metadata["reflection_input"]["assistant_output"]


def test_executive_brain_emits_turn_and_deep_reflection_for_terminal_result() -> None:
    asyncio.run(_exercise_terminal_reflection())


async def _exercise_progress_reply() -> None:
    bus = PriorityPubSubBus()
    store = _task_store()
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    replies: list[BusEnvelope[BrainReplyPayload]] = []

    async def _capture(event: BusEnvelope[BrainReplyPayload]) -> None:
        replies.append(event)

    bus.subscribe(consumer="runtime", event_type=EventType.BRAIN_REPLY, handler=_capture)

    task = store.require("task_1")
    task.status = TaskStatus.RUNNING
    task.touch()

    await bus.publish(
        build_envelope(
            event_type=EventType.TASK_EVENT_PROGRESS,
            source="runtime",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=TaskProgressEventPayload(
                task_id="task_1",
                state=task.snapshot(),
                summary="write_file 已完成：Wrote 31 characters to add.py",
                detail="tool",
                metadata={"tool_name": "write_file"},
            ),
        )
    )
    await bus.drain()

    assert len(replies) == 1
    assert "进展" in replies[0].payload.reply.plain_text
    assert "write_file 已完成" in replies[0].payload.reply.plain_text


def test_executive_brain_emits_progress_reply_for_tool_progress() -> None:
    asyncio.run(_exercise_progress_reply())
