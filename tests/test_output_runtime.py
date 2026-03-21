from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.output.runtime import OutputRuntime
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    BrainReplyReadyPayload,
    BrainStreamDeltaPayload,
    OutputReadyPayloadBase,
)
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType


def _origin(message_id: str) -> MessageRef:
    return MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id=message_id)


def _inline_target() -> DeliveryTargetPayload:
    return DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct")


def _stream_target() -> DeliveryTargetPayload:
    return DeliveryTargetPayload(delivery_mode="stream", channel="cli", chat_id="direct")


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_output_runtime_builds_inline_reply() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    inline: list[BusEnvelope[OutputReadyPayloadBase]] = []

    async def _capture_inline(event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        inline.append(event)

    bus.subscribe(consumer="test:inline", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture_inline)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_1",
                reply_text="你好",
                delivery_target=_inline_target(),
                origin_message=_origin("msg_1"),
            ),
        )
    )
    await _drain(bus)

    assert len(inline) == 1
    assert inline[0].payload.content.plain_text == "你好"
    assert inline[0].payload.content.reply_to_message_id == "msg_1"
    assert inline[0].payload.delivery_target.delivery_mode == "inline"
    assert inline[0].payload.origin_message.message_id == "msg_1"


def test_output_runtime_builds_inline_reply() -> None:
    asyncio.run(_exercise_output_runtime_builds_inline_reply())


async def _exercise_output_runtime_builds_stream_events() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    events: list[BusEnvelope[OutputReadyPayloadBase]] = []

    async def _capture(event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        events.append(event)

    bus.subscribe(consumer="test:stream-open", event_type=EventType.OUTPUT_STREAM_OPEN, handler=_capture)
    bus.subscribe(consumer="test:stream-delta", event_type=EventType.OUTPUT_STREAM_DELTA, handler=_capture)
    bus.subscribe(consumer="test:stream-close", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_STREAM_DELTA_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=BrainStreamDeltaPayload(
                stream_id="stream_1",
                delta_text="你好。",
                stream_state="open",
                stream_index=1,
                origin_message=_origin("msg_stream"),
            ),
        )
    )
    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_STREAM_DELTA_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=BrainStreamDeltaPayload(
                stream_id="stream_1",
                delta_text="我在这。",
                stream_state="delta",
                stream_index=2,
                origin_message=_origin("msg_stream"),
            ),
        )
    )
    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_stream",
                reply_text="你好。我在这。",
                delivery_target=_stream_target(),
                origin_message=_origin("msg_stream"),
                stream_id="stream_1",
                stream_state="close",
            ),
        )
    )
    await _drain(bus)

    assert [event.event_type for event in events] == [
        EventType.OUTPUT_STREAM_OPEN,
        EventType.OUTPUT_STREAM_DELTA,
        EventType.OUTPUT_STREAM_CLOSE,
    ]
    assert [event.payload.content.plain_text for event in events] == ["你好。", "我在这。", "你好。我在这。"]
    assert all(event.payload.delivery_target.delivery_mode == "stream" for event in events)
    assert events[-1].payload.stream_state == "close"


def test_output_runtime_builds_stream_events() -> None:
    asyncio.run(_exercise_output_runtime_builds_stream_events())


async def _exercise_output_runtime_builds_executor_result_push() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    pushed: list[BusEnvelope[OutputReadyPayloadBase]] = []

    async def _capture_push(event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        pushed.append(event)

    bus.subscribe(consumer="test:push", event_type=EventType.OUTPUT_PUSH_READY, handler=_capture_push)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_2",
            turn_id="turn_2",
            task_id="task_2",
            correlation_id="task_2",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_2",
                reply_text="任务完成。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="telegram", chat_id="123456"),
                origin_message=_origin("msg_2"),
                related_task_id="task_2",
                metadata={
                    "brain_source": "executor_result",
                    "source_event": EventType.EXECUTOR_EVENT_RESULT_READY,
                    "source_decision": "accept",
                    "job_id": "job_2",
                    "result": "success",
                },
            ),
        )
    )
    await _drain(bus)

    assert len(pushed) == 1
    assert pushed[0].payload.content.plain_text == "任务完成。"
    assert pushed[0].payload.delivery_target.delivery_mode == "push"
    assert pushed[0].payload.delivery_target.channel == "telegram"
    assert pushed[0].payload.delivery_target.chat_id == "123456"
    assert pushed[0].payload.origin_message.message_id == "msg_2"


def test_output_runtime_builds_executor_result_push() -> None:
    asyncio.run(_exercise_output_runtime_builds_executor_result_push())


async def _exercise_output_runtime_drops_blocked_reply_without_fallback() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    inline: list[BusEnvelope[OutputReadyPayloadBase]] = []

    async def _capture_inline(event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        inline.append(event)

    bus.subscribe(consumer="test:inline", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture_inline)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_unsafe",
            turn_id="turn_unsafe",
            correlation_id="turn_unsafe",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_unsafe",
                reply_text="-----BEGIN PRIVATE KEY-----",
                delivery_target=_inline_target(),
                origin_message=_origin("msg_unsafe"),
            ),
        )
    )
    await _drain(bus)

    assert inline == []


def test_output_runtime_drops_blocked_reply_without_fallback() -> None:
    asyncio.run(_exercise_output_runtime_drops_blocked_reply_without_fallback())


async def _exercise_output_runtime_skips_suppressed_left_reply() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    seen: list[str] = []

    async def _capture(event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        seen.append(str(event.event_type))

    bus.subscribe(consumer="test:inline", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture)
    bus.subscribe(consumer="test:push", event_type=EventType.OUTPUT_PUSH_READY, handler=_capture)
    bus.subscribe(consumer="test:stream-open", event_type=EventType.OUTPUT_STREAM_OPEN, handler=_capture)
    bus.subscribe(consumer="test:stream-delta", event_type=EventType.OUTPUT_STREAM_DELTA, handler=_capture)
    bus.subscribe(consumer="test:stream-close", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_sync",
            turn_id="turn_sync",
            correlation_id="turn_sync",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_sync",
                reply_text="这条不应该出现在 output 层",
                delivery_target=_inline_target(),
                origin_message=_origin("msg_sync"),
                metadata={"suppress_output": True},
            ),
        )
    )
    await _drain(bus)

    assert seen == []


def test_output_runtime_skips_suppressed_left_reply() -> None:
    asyncio.run(_exercise_output_runtime_skips_suppressed_left_reply())


async def _exercise_output_runtime_skips_suppressed_executor_result_reply() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    seen: list[str] = []

    async def _capture(event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        seen.append(str(event.event_type))

    bus.subscribe(consumer="test:inline-executor-result", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture)
    bus.subscribe(consumer="test:push-executor-result", event_type=EventType.OUTPUT_PUSH_READY, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.BRAIN_EVENT_REPLY_READY,
            source="brain_runtime",
            target="broadcast",
            session_id="sess_sync_executor_result",
            turn_id="turn_sync_executor_result",
            correlation_id="turn_sync_executor_result",
            payload=BrainReplyReadyPayload(
                request_id="brain_reply_sync_executor_result",
                reply_text="这条同步进展不应该投递给用户",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="inline", channel="cli", chat_id="direct"),
                origin_message=_origin("msg_sync_executor_result"),
                metadata={
                    "brain_source": "executor_result",
                    "source_event": EventType.EXECUTOR_EVENT_RESULT_READY,
                    "source_decision": "accept",
                    "job_id": "job_sync_executor_result",
                    "suppress_output": True,
                },
            ),
        )
    )
    await _drain(bus)

    assert seen == []


def test_output_runtime_skips_suppressed_executor_result_reply() -> None:
    asyncio.run(_exercise_output_runtime_skips_suppressed_executor_result_reply())
