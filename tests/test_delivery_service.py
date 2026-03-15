from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.delivery.service import DeliveryService
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import DeliveryFailedPayload, RepliedPayload, ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus


async def _exercise_delivery_success() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport)
    replied: list[BusEnvelope[RepliedPayload]] = []

    service.register()

    async def _capture(event: BusEnvelope[RepliedPayload]) -> None:
        replied.append(event)

    bus.subscribe(consumer="test", event_type=EventType.OUTPUT_REPLIED, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_REPLY_APPROVED,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=ReplyReadyPayload(
                reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done"),
                origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            ),
        )
    )
    await bus.drain()

    assert len(replied) == 1
    assert replied[0].payload.reply_id == "reply_1"
    assert replied[0].payload.delivery_message.message_id == "delivery_reply_1"
    assert replied[0].payload.delivered_at is not None

    outbound = await transport.consume_outbound()
    assert outbound.message_id == "delivery_reply_1"
    assert outbound.content == "done"


def test_delivery_service_emits_replied_after_transport_publish() -> None:
    asyncio.run(_exercise_delivery_success())


async def _exercise_delivery_without_transport() -> None:
    bus = PriorityPubSubBus()
    service = DeliveryService(bus=bus, transport=None)
    failed: list[BusEnvelope[DeliveryFailedPayload]] = []
    replied: list[BusEnvelope[RepliedPayload]] = []

    service.register()

    async def _capture_failed(event: BusEnvelope[DeliveryFailedPayload]) -> None:
        failed.append(event)

    async def _capture_replied(event: BusEnvelope[RepliedPayload]) -> None:
        replied.append(event)

    bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_DELIVERY_FAILED, handler=_capture_failed)
    bus.subscribe(consumer="test:replied", event_type=EventType.OUTPUT_REPLIED, handler=_capture_replied)

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_REPLY_APPROVED,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=ReplyReadyPayload(
                reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done"),
                origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
            ),
        )
    )
    await bus.drain()

    assert len(replied) == 0
    assert len(failed) == 1
    assert failed[0].payload.reason == "delivery_transport_unavailable"
    assert failed[0].payload.retryable is False


def test_delivery_service_without_transport_emits_failure() -> None:
    asyncio.run(_exercise_delivery_without_transport())
