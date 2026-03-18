from __future__ import annotations

import asyncio

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.output.runtime import OutputRuntime
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    LeftFollowupReadyPayload,
    LeftReplyReadyPayload,
    LeftStreamDeltaPayload,
    ReplyReadyPayload,
)
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType


def _origin(message_id: str) -> MessageRef:
    return MessageRef(channel="cli", chat_id="direct", sender_id="user", message_id=message_id)


async def _drain(bus: PriorityPubSubBus) -> None:
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()


async def _exercise_output_runtime_builds_inline_reply() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    inline: list[BusEnvelope[ReplyReadyPayload]] = []

    async def _capture_inline(event: BusEnvelope[ReplyReadyPayload]) -> None:
        inline.append(event)

    bus.subscribe(consumer="test:inline", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture_inline)

    await bus.publish(
        build_envelope(
            event_type=EventType.LEFT_EVENT_REPLY_READY,
            source="brain",
            target="broadcast",
            session_id="sess_1",
            turn_id="turn_1",
            correlation_id="turn_1",
            payload=LeftReplyReadyPayload(
                request_id="left_reply_1",
                reply_text="你好",
                origin_message=_origin("msg_1"),
            ),
        )
    )
    await _drain(bus)

    assert len(inline) == 1
    assert inline[0].payload.reply.plain_text == "你好"
    assert inline[0].payload.reply.reply_to_message_id == "msg_1"
    assert inline[0].payload.delivery_mode == "inline"
    assert inline[0].payload.origin_message.message_id == "msg_1"


def test_output_runtime_builds_inline_reply() -> None:
    asyncio.run(_exercise_output_runtime_builds_inline_reply())


async def _exercise_output_runtime_builds_stream_events() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    events: list[BusEnvelope[ReplyReadyPayload]] = []

    async def _capture(event: BusEnvelope[ReplyReadyPayload]) -> None:
        events.append(event)

    bus.subscribe(consumer="test:stream-open", event_type=EventType.OUTPUT_STREAM_OPEN, handler=_capture)
    bus.subscribe(consumer="test:stream-delta", event_type=EventType.OUTPUT_STREAM_DELTA, handler=_capture)
    bus.subscribe(consumer="test:stream-close", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.LEFT_EVENT_STREAM_DELTA_READY,
            source="brain",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=LeftStreamDeltaPayload(
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
            event_type=EventType.LEFT_EVENT_STREAM_DELTA_READY,
            source="brain",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=LeftStreamDeltaPayload(
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
            event_type=EventType.LEFT_EVENT_REPLY_READY,
            source="brain",
            target="broadcast",
            session_id="sess_stream",
            turn_id="turn_stream",
            correlation_id="turn_stream",
            payload=LeftReplyReadyPayload(
                request_id="left_reply_stream",
                reply_text="你好。我在这。",
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
    assert [event.payload.reply.plain_text for event in events] == ["你好。", "我在这。", "你好。我在这。"]
    assert all(event.payload.delivery_mode == "stream" for event in events)
    assert events[-1].payload.stream_state == "close"


def test_output_runtime_builds_stream_events() -> None:
    asyncio.run(_exercise_output_runtime_builds_stream_events())


async def _exercise_output_runtime_builds_followup_push() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    pushed: list[BusEnvelope[ReplyReadyPayload]] = []

    async def _capture_push(event: BusEnvelope[ReplyReadyPayload]) -> None:
        pushed.append(event)

    bus.subscribe(consumer="test:push", event_type=EventType.OUTPUT_PUSH_READY, handler=_capture_push)

    await bus.publish(
        build_envelope(
            event_type=EventType.LEFT_EVENT_FOLLOWUP_READY,
            source="brain",
            target="broadcast",
            session_id="sess_2",
            turn_id="turn_2",
            task_id="task_2",
            correlation_id="task_2",
            payload=LeftFollowupReadyPayload(
                job_id="job_2",
                source_event=EventType.RIGHT_EVENT_RESULT_READY,
                source_decision="accept",
                reply_text="任务完成。",
                reply_kind="status",
                delivery_target=DeliveryTargetPayload(delivery_mode="push"),
                origin_message=_origin("msg_2"),
                related_task_id="task_2",
                metadata={"result": "success"},
            ),
        )
    )
    await _drain(bus)

    assert len(pushed) == 1
    assert pushed[0].payload.reply.plain_text == "任务完成。"
    assert pushed[0].payload.delivery_mode == "push"
    assert pushed[0].payload.origin_message.message_id == "msg_2"


def test_output_runtime_builds_followup_push() -> None:
    asyncio.run(_exercise_output_runtime_builds_followup_push())


async def _exercise_output_runtime_emits_safe_fallback_for_blocked_reply() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    inline: list[BusEnvelope[ReplyReadyPayload]] = []

    async def _capture_inline(event: BusEnvelope[ReplyReadyPayload]) -> None:
        inline.append(event)

    bus.subscribe(consumer="test:inline", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture_inline)

    await bus.publish(
        build_envelope(
            event_type=EventType.LEFT_EVENT_REPLY_READY,
            source="brain",
            target="broadcast",
            session_id="sess_unsafe",
            turn_id="turn_unsafe",
            correlation_id="turn_unsafe",
            payload=LeftReplyReadyPayload(
                request_id="left_reply_unsafe",
                reply_text="-----BEGIN PRIVATE KEY-----",
                origin_message=_origin("msg_unsafe"),
            ),
        )
    )
    await _drain(bus)

    assert len(inline) == 1
    assert inline[0].payload.reply.safe_fallback is True
    assert inline[0].payload.reply.kind == "safety_fallback"
    assert "不能直接发出" in str(inline[0].payload.reply.plain_text or "")


def test_output_runtime_emits_safe_fallback_for_blocked_reply() -> None:
    asyncio.run(_exercise_output_runtime_emits_safe_fallback_for_blocked_reply())


async def _exercise_output_runtime_skips_suppressed_left_reply() -> None:
    bus = PriorityPubSubBus()
    runtime = OutputRuntime(bus=bus)
    runtime.register()

    seen: list[str] = []

    async def _capture(event: BusEnvelope[ReplyReadyPayload]) -> None:
        seen.append(str(event.event_type))

    bus.subscribe(consumer="test:inline", event_type=EventType.OUTPUT_INLINE_READY, handler=_capture)
    bus.subscribe(consumer="test:push", event_type=EventType.OUTPUT_PUSH_READY, handler=_capture)
    bus.subscribe(consumer="test:stream-open", event_type=EventType.OUTPUT_STREAM_OPEN, handler=_capture)
    bus.subscribe(consumer="test:stream-delta", event_type=EventType.OUTPUT_STREAM_DELTA, handler=_capture)
    bus.subscribe(consumer="test:stream-close", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=_capture)

    await bus.publish(
        build_envelope(
            event_type=EventType.LEFT_EVENT_REPLY_READY,
            source="brain",
            target="broadcast",
            session_id="sess_sync",
            turn_id="turn_sync",
            correlation_id="turn_sync",
            payload=LeftReplyReadyPayload(
                request_id="left_reply_sync",
                reply_text="这条不应该出现在 output 层",
                origin_message=_origin("msg_sync"),
                metadata={"suppress_output": True},
            ),
        )
    )
    await _drain(bus)

    assert seen == []


def test_output_runtime_skips_suppressed_left_reply() -> None:
    asyncio.run(_exercise_output_runtime_skips_suppressed_left_reply())
