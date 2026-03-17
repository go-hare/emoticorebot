from __future__ import annotations

import asyncio

from emoticorebot.brain.executive import ExecutiveBrain
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import TaskCancelPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import InterruptPayload, TaskEndPayload
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
                summary="已完成",
                output="任务已经完成。",
            ),
        )
    )
    await bus.drain()

    assert len(turn_events) == 1
    assert turn_events[0].payload.reason == "task_result"
    assert "reflection_input" in turn_events[0].payload.metadata
    task_projection = turn_events[0].payload.metadata["reflection_input"]["task"]
    assert task_projection["state"] == "done"
    assert task_projection["result"] == "success"
    assert len(deep_events) == 1
    assert deep_events[0].payload.reason == "task_result"
    assert deep_events[0].payload.metadata["reflection_input"]["assistant_output"]


def test_executive_brain_emits_turn_and_deep_reflection_for_terminal_result() -> None:
    asyncio.run(_exercise_terminal_reflection())


async def _exercise_interrupt_does_not_cancel_active_task() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_running",
            session_id="sess_1",
            turn_id="turn_1",
            request=TaskRequestSpec(request="完成任务", title="完成任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            title="完成任务",
            status=TaskStatus.RUNNING,
            assignee="worker",
        )
    )
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    cancels: list[BusEnvelope[TaskCancelPayload]] = []

    async def _capture(event: BusEnvelope[TaskCancelPayload]) -> None:
        cancels.append(event)

    bus.subscribe(consumer="runtime", event_type=EventType.TASK_CANCEL, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_INTERRUPT,
            source="gateway",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_running",
            correlation_id="task_running",
            payload=InterruptPayload(
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_2"),
                interrupt_type="new_user_message",
                plain_text="先停一下",
                target_task_id="task_running",
                urgent=True,
            ),
        )
    )
    await bus.drain()

    assert len(cancels) == 0


def test_executive_brain_interrupt_does_not_cancel_active_task_by_default() -> None:
    asyncio.run(_exercise_interrupt_does_not_cancel_active_task())


async def _exercise_interrupt_cancels_active_task_when_requested() -> None:
    bus = PriorityPubSubBus()
    store = TaskStore()
    store.add(
        RuntimeTaskRecord(
            task_id="task_running",
            session_id="sess_1",
            turn_id="turn_1",
            request=TaskRequestSpec(request="完成任务", title="完成任务"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            title="完成任务",
            status=TaskStatus.RUNNING,
            assignee="worker",
        )
    )
    brain = ExecutiveBrain(bus=bus, task_store=store)
    brain.register()

    cancels: list[BusEnvelope[TaskCancelPayload]] = []

    async def _capture(event: BusEnvelope[TaskCancelPayload]) -> None:
        cancels.append(event)

    bus.subscribe(consumer="runtime", event_type=EventType.TASK_CANCEL, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.INPUT_INTERRUPT,
            source="gateway",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_running",
            correlation_id="task_running",
            payload=InterruptPayload(
                message=MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id="msg_2"),
                interrupt_type="new_user_message",
                plain_text="停掉它",
                target_task_id="task_running",
                urgent=True,
                metadata={"cancel_active_task": True},
            ),
        )
    )
    await bus.drain()

    assert len(cancels) == 1
    assert cancels[0].payload.task_id == "task_running"
    assert cancels[0].payload.reason == "interrupted_by_new_user_message"


def test_executive_brain_interrupt_cancels_active_task_when_requested() -> None:
    asyncio.run(_exercise_interrupt_cancels_active_task_when_requested())
