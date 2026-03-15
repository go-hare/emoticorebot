from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.delivery.service import DeliveryService
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload, TaskResultEventPayload
from emoticorebot.protocol.task_models import ContentBlock, MessageRef, ReplyDraft, TaskStateSnapshot
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.safety.guard import SafetyGuard


async def _exercise_guard_redaction() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    guard = SafetyGuard(bus=bus)
    delivery = DeliveryService(bus=bus, transport=transport)
    captured: list[BusEnvelope[ReplyReadyPayload]] = []

    guard.register()
    delivery.register()

    async def _capture(event: BusEnvelope[ReplyReadyPayload]) -> None:
        captured.append(event)

    bus.subscribe(consumer="test", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=_capture)

    reply = ReplyDraft(reply_id="reply_1", kind="answer", plain_text="api_key=sk-abcdefghijklmnopqrstuv")
    event = build_envelope(
        event_type=EventType.OUTPUT_REPLY_READY,
        source="runtime",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        correlation_id="turn_1",
        payload=ReplyReadyPayload(
            reply=reply,
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        ),
    )

    await bus.publish(event)
    await bus.drain()

    assert len(captured) == 1
    assert captured[0].payload.reply.plain_text == "api_key=[REDACTED]"
    outbound = await transport.consume_outbound()
    assert outbound.content == "api_key=[REDACTED]"


def test_safety_guard_redacts_sensitive_reply() -> None:
    asyncio.run(_exercise_guard_redaction())


async def _exercise_guard_redaction_for_content_blocks() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    guard = SafetyGuard(bus=bus)
    delivery = DeliveryService(bus=bus, transport=transport)
    captured: list[BusEnvelope[ReplyReadyPayload]] = []

    guard.register()
    delivery.register()

    async def _capture(event: BusEnvelope[ReplyReadyPayload]) -> None:
        captured.append(event)

    bus.subscribe(consumer="test", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=_capture)

    event = build_envelope(
        event_type=EventType.OUTPUT_REPLY_READY,
        source="runtime",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        correlation_id="turn_1",
        payload=ReplyReadyPayload(
            reply=ReplyDraft(
                reply_id="reply_2",
                kind="answer",
                content_blocks=[ContentBlock(type="text", text="password=secret123")],
            ),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        ),
    )

    await bus.publish(event)
    await bus.drain()

    assert len(captured) == 1
    assert captured[0].payload.reply.content_blocks[0].text == "password=[REDACTED]"
    outbound = await transport.consume_outbound()
    assert outbound.content == "password=[REDACTED]"


def test_safety_guard_redacts_sensitive_reply_blocks() -> None:
    asyncio.run(_exercise_guard_redaction_for_content_blocks())


async def _exercise_guard_redaction_for_task_blocks() -> None:
    bus = PriorityPubSubBus()
    guard = SafetyGuard(bus=bus)
    captured: list[BusEnvelope[TaskResultEventPayload]] = []

    guard.register()

    async def _capture(event: BusEnvelope[TaskResultEventPayload]) -> None:
        captured.append(event)

    bus.subscribe(consumer="test", event_type=EventType.TASK_EVENT_RESULT, handler=_capture)

    event = build_envelope(
        event_type=EventType.TASK_EVENT_RESULT,
        source="runtime",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=TaskResultEventPayload(
            task_id="task_1",
            state=TaskStateSnapshot(task_id="task_1", status="done"),
            summary="done",
            result_blocks=[ContentBlock(type="text", text="api_key=sk-abcdefghijklmnopqrstuv")],
            artifacts=[ContentBlock(type="text", text="password=secret123")],
        ),
    )

    await bus.publish(event)
    await bus.drain()

    assert len(captured) == 1
    assert captured[0].payload.result_blocks[0].text == "api_key=[REDACTED]"
    assert captured[0].payload.artifacts[0].text == "password=[REDACTED]"


def test_safety_guard_redacts_sensitive_task_blocks() -> None:
    asyncio.run(_exercise_guard_redaction_for_task_blocks())
