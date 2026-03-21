from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.delivery.service import DeliveryService
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryFailedPayload,
    DeliveryTargetPayload,
    OutputInlineReadyPayload,
    OutputStreamClosePayload,
    OutputStreamDeltaPayload,
    OutputStreamOpenPayload,
    RepliedPayload,
)
from emoticorebot.protocol.task_models import ContentBlock, MessageRef, ReplyDraft
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus


def _inline_payload(*, reply: ReplyDraft, message_id: str = "msg_1") -> OutputInlineReadyPayload:
    return OutputInlineReadyPayload(
        output_id=f"out_{reply.reply_id}",
        delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
        content=reply,
        origin_message=MessageRef(channel="cli", chat_id="direct", message_id=message_id),
    )


def _stream_payload(
    *,
    event_type: str,
    reply: ReplyDraft,
    stream_state: str,
    stream_index: int,
    message_id: str = "msg_1",
    metadata: dict[str, object] | None = None,
) -> OutputStreamOpenPayload | OutputStreamDeltaPayload | OutputStreamClosePayload:
    common = {
        "output_id": f"out_{reply.reply_id}",
        "delivery_target": DeliveryTargetPayload(delivery_mode="stream", channel="cli", chat_id="direct"),
        "content": reply,
        "origin_message": MessageRef(channel="cli", chat_id="direct", message_id=message_id),
        "metadata": dict(metadata or {}),
        "stream_id": "stream_turn_1",
        "stream_index": stream_index,
    }
    if event_type == EventType.OUTPUT_STREAM_OPEN:
        return OutputStreamOpenPayload(stream_state=stream_state, **common)
    if event_type == EventType.OUTPUT_STREAM_CLOSE:
        return OutputStreamClosePayload(stream_state=stream_state, **common)
    return OutputStreamDeltaPayload(stream_state=stream_state, **common)


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
            event_type=EventType.OUTPUT_INLINE_READY,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=_inline_payload(reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done")),
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
    assert outbound.metadata["reply_kind"] == "answer"


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
            event_type=EventType.OUTPUT_INLINE_READY,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=_inline_payload(reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done")),
        )
    )
    await bus.drain()

    assert len(replied) == 0
    assert len(failed) == 1
    assert failed[0].payload.reason == "delivery_transport_unavailable"


def test_delivery_service_without_transport_emits_failure() -> None:
    asyncio.run(_exercise_delivery_without_transport())


async def _exercise_delivery_drops_stale_reply() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport, should_deliver=lambda _event: False)
    failed: list[BusEnvelope[DeliveryFailedPayload]] = []

    service.register()

    async def _capture_failed(event: BusEnvelope[DeliveryFailedPayload]) -> None:
        failed.append(event)

    bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_DELIVERY_FAILED, handler=_capture_failed)

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_INLINE_READY,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_stale",
            payload=_inline_payload(reply=ReplyDraft(reply_id="reply_stale", kind="answer", plain_text="old")),
        )
    )
    await bus.drain()

    assert transport.outbound_size == 0
    assert len(failed) == 1
    assert failed[0].payload.reason == "stale_reply_dropped"


def test_delivery_service_drops_stale_reply() -> None:
    asyncio.run(_exercise_delivery_drops_stale_reply())


async def _exercise_delivery_suppressed_reply_emits_replied_without_transport() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport)
    replied: list[BusEnvelope[RepliedPayload]] = []

    service.register()

    async def _capture_replied(event: BusEnvelope[RepliedPayload]) -> None:
        replied.append(event)

    bus.subscribe(consumer="test", event_type=EventType.OUTPUT_REPLIED, handler=_capture_replied)

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_INLINE_READY,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            task_id="task_1",
            correlation_id="task_1",
            payload=_inline_payload(
                reply=ReplyDraft(
                    reply_id="reply_suppressed",
                    kind="answer",
                    plain_text="done",
                    metadata={"suppress_delivery": True},
                ),
            ),
        )
    )
    await bus.drain()

    assert transport.outbound_size == 0
    assert len(replied) == 1
    assert replied[0].payload.reply_id == "reply_suppressed"
    assert replied[0].payload.delivery_mode == "suppressed"
    assert replied[0].payload.delivery_message.message_id == "suppressed_reply_suppressed"


def test_delivery_service_suppressed_reply_emits_replied_without_transport() -> None:
    asyncio.run(_exercise_delivery_suppressed_reply_emits_replied_without_transport())


async def _exercise_delivery_stream_delta_skips_replied_event() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport)
    replied: list[BusEnvelope[RepliedPayload]] = []

    service.register()

    async def _capture_replied(event: BusEnvelope[RepliedPayload]) -> None:
        replied.append(event)

    bus.subscribe(consumer="test", event_type=EventType.OUTPUT_REPLIED, handler=_capture_replied)

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_STREAM_OPEN,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            payload=_stream_payload(
                event_type=EventType.OUTPUT_STREAM_OPEN,
                reply=ReplyDraft(
                    reply_id="reply_stream_1",
                    kind="answer",
                    plain_text="你",
                ),
                stream_state="open",
                stream_index=1,
            ),
        )
    )
    await bus.drain()

    outbound = await transport.consume_outbound()
    assert outbound.content == "你"
    assert outbound.metadata["reply_kind"] == "answer"
    assert outbound.metadata["_stream"] is True
    assert outbound.metadata["_stream_state"] == "open"
    assert replied == []


def test_delivery_service_stream_delta_skips_replied_event() -> None:
    asyncio.run(_exercise_delivery_stream_delta_skips_replied_event())


async def _exercise_delivery_stream_stale_reply_emits_superseded() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport, should_deliver=lambda _event: False)

    service.register()

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_STREAM_DELTA,
            source="safety",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            payload=_stream_payload(
                event_type=EventType.OUTPUT_STREAM_DELTA,
                reply=ReplyDraft(
                    reply_id="reply_stream_stale",
                    kind="answer",
                    plain_text="旧内容",
                ),
                stream_state="delta",
                stream_index=2,
            ),
        )
    )
    await bus.drain()

    outbound = await transport.consume_outbound()
    assert outbound.metadata["_stream"] is True
    assert outbound.metadata["_stream_state"] == "superseded"


def test_delivery_service_stream_stale_reply_emits_superseded() -> None:
    asyncio.run(_exercise_delivery_stream_stale_reply_emits_superseded())


async def _exercise_delivery_stream_close_avoids_duplicate_body() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport)
    replied: list[BusEnvelope[RepliedPayload]] = []

    service.register()

    async def _capture_replied(event: BusEnvelope[RepliedPayload]) -> None:
        replied.append(event)

    bus.subscribe(consumer="test", event_type=EventType.OUTPUT_REPLIED, handler=_capture_replied)

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_STREAM_CLOSE,
            source="safety",
            target="broadcast",
            session_id="sess_stream_close",
            turn_id="turn_stream_close",
            payload=_stream_payload(
                event_type=EventType.OUTPUT_STREAM_CLOSE,
                reply=ReplyDraft(
                    reply_id="reply_stream_close",
                    kind="answer",
                    plain_text="你好。我在这。",
                    metadata={"stream_close_without_body": True},
                ),
                stream_state="close",
                stream_index=3,
            ),
        )
    )
    await bus.drain()

    outbound = await transport.consume_outbound()
    assert outbound.content == ""
    assert outbound.content_blocks == []
    assert outbound.media == []
    assert outbound.metadata["_stream_state"] == "close"
    assert len(replied) == 1
    assert replied[0].payload.reply_id == "reply_stream_close"


def test_delivery_service_stream_close_avoids_duplicate_body() -> None:
    asyncio.run(_exercise_delivery_stream_close_avoids_duplicate_body())


async def _exercise_delivery_preserves_multimodal_blocks() -> None:
    bus = PriorityPubSubBus()
    transport = TransportBus()
    service = DeliveryService(bus=bus, transport=transport)

    service.register()

    await bus.publish(
        build_envelope(
            event_type=EventType.OUTPUT_INLINE_READY,
            source="safety",
            target="broadcast",
            session_id="sess_media",
            turn_id="turn_media",
            payload=_inline_payload(
                reply=ReplyDraft(
                    reply_id="reply_media",
                    kind="answer",
                    content_blocks=[
                        ContentBlock(type="text", text="看这个"),
                        ContentBlock(type="image", path="/tmp/example.png", mime_type="image/png"),
                        ContentBlock(type="link", url="https://example.com/reference"),
                    ],
                ),
                message_id="msg_media",
            ),
        )
    )
    await bus.drain()

    outbound = await transport.consume_outbound()
    assert outbound.content == "看这个"
    assert outbound.media == ["/tmp/example.png"]
    assert outbound.content_blocks[0]["type"] == "text"
    assert outbound.content_blocks[1]["path"] == "/tmp/example.png"
    assert outbound.content_blocks[2]["url"] == "https://example.com/reference"


def test_delivery_service_preserves_multimodal_blocks() -> None:
    asyncio.run(_exercise_delivery_preserves_multimodal_blocks())
