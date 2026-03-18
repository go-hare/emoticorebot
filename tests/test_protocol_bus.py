from __future__ import annotations

import asyncio

import pytest

from emoticorebot.bus import BackpressureController, BackpressureError, PriorityPubSubBus, block, redact
from emoticorebot.protocol.commands import TaskCancelPayload
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload, SystemSignalPayload, TurnInputPayload
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft
from emoticorebot.protocol.topics import EventType, Topic


def _reply_event(*, reply_id: str, target: str = "broadcast", dedupe_key: str | None = None, text: str = "hi"):
    return build_envelope(
        event_type=EventType.OUTPUT_INLINE_READY,
        source="runtime",
        target=target,
        session_id="sess_1",
        payload=ReplyReadyPayload(reply=ReplyDraft(reply_id=reply_id, kind="answer", plain_text=text)),
        dedupe_key=dedupe_key,
    )


def test_priority_bus_dispatches_higher_priority_first() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus()
        seen: list[str] = []

        async def handler(event):
            seen.append(event.event_type)

        bus.subscribe(consumer="brain", handler=handler, topic=Topic.INPUT_EVENT)
        bus.subscribe(consumer="runtime", handler=handler, topic=Topic.TASK_COMMAND)

        low = build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            payload=TurnInputPayload(
                input_id="turn_1",
                input_mode="turn",
                session_mode="turn_chat",
                message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
                user_text="hello",
                metadata={"channel_kind": "chat"},
            ),
        )
        high = build_envelope(
            event_type=EventType.TASK_CANCEL,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=TaskCancelPayload(command_id="cmd_1", task_id="task_1"),
        )

        await bus.publish(low)
        await bus.publish(high)
        await bus.drain()

        assert seen == [EventType.TASK_CANCEL, EventType.INPUT_TURN_RECEIVED]

    asyncio.run(_run())


def test_target_routing_only_delivers_to_matching_consumer() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus()
        seen: list[str] = []

        async def worker_handler(event):
            seen.append(f"worker:{event.target}")

        async def brain_handler(event):
            seen.append(f"brain:{event.target}")

        bus.subscribe(consumer="worker", handler=worker_handler, topic=Topic.OUTPUT_EVENT)
        bus.subscribe(consumer="brain", handler=brain_handler, topic=Topic.OUTPUT_EVENT)

        await bus.publish(_reply_event(reply_id="reply_1", target="worker"))
        await bus.drain()

        assert seen == ["worker:worker"]

    asyncio.run(_run())


def test_interceptor_can_redact_and_block() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus()
        delivered: list[str] = []
        audits: list[str] = []

        async def delivery(event):
            delivered.append(event.payload.reply.plain_text or "")

        async def audit(event):
            audits.append(event.event_type)

        async def interceptor(outcome):
            text = outcome.event.payload.reply.plain_text or ""
            if "secret" in text:
                audit_event = build_envelope(
                    event_type=EventType.SYSTEM_HEALTH_WARNING,
                    source="guard",
                    target="broadcast",
                    session_id=outcome.event.session_id,
                    payload=SystemSignalPayload(
                        signal_id="signal_block",
                        signal_type="health_warning",
                        reason="reply_blocked",
                        related_event_id=outcome.event.event_id,
                        severity="warning",
                    ),
                )
                return block(outcome.event, audit_event)
            redacted_event = outcome.event.model_copy(
                update={
                    "payload": ReplyReadyPayload(
                        reply=ReplyDraft(
                            reply_id=outcome.event.payload.reply.reply_id,
                            kind=outcome.event.payload.reply.kind,
                            plain_text="[REDACTED]",
                        )
                    )
                }
            )
            audit_event = build_envelope(
                event_type=EventType.SYSTEM_WARNING,
                source="guard",
                target="broadcast",
                session_id=outcome.event.session_id,
                payload=SystemSignalPayload(
                    signal_id="signal_redact",
                    signal_type="warning",
                    reason="reply_redacted",
                    related_event_id=outcome.event.event_id,
                    severity="warning",
                ),
            )
            return redact(redacted_event, audit_event)

        bus.register_interceptor(topic=Topic.OUTPUT_EVENT, handler=interceptor)
        bus.subscribe(consumer="delivery", handler=delivery, topic=Topic.OUTPUT_EVENT)
        bus.subscribe(consumer="audit", handler=audit, topic=Topic.SYSTEM_SIGNAL)

        await bus.publish(_reply_event(reply_id="reply_2", text="contains token"))
        await bus.publish(_reply_event(reply_id="reply_3", text="contains secret"))
        await bus.drain()

        assert delivered == ["[REDACTED]"]
        assert audits == [EventType.SYSTEM_WARNING, EventType.SYSTEM_HEALTH_WARNING]

    asyncio.run(_run())


def test_dedupe_key_drops_duplicates() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus()
        seen: list[str] = []

        async def delivery(event):
            seen.append(event.payload.reply.reply_id)

        bus.subscribe(consumer="delivery", handler=delivery, topic=Topic.OUTPUT_EVENT)

        assert await bus.publish(_reply_event(reply_id="reply_4", dedupe_key="same")) is True
        assert await bus.publish(_reply_event(reply_id="reply_5", dedupe_key="same")) is False
        await bus.drain()

        assert seen == ["reply_4"]

    asyncio.run(_run())


def test_backpressure_warning_is_emitted_before_hard_limit() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus(backpressure=BackpressureController(warning_threshold=1, max_queue_size=2))
        seen: list[str] = []

        async def system_handler(event):
            seen.append(event.event_type)

        bus.subscribe(consumer="observer", handler=system_handler, topic=Topic.SYSTEM_SIGNAL)

        await bus.publish(_reply_event(reply_id="reply_6"))
        await bus.drain()

        assert EventType.SYSTEM_BACKPRESSURE in seen

    asyncio.run(_run())


def test_backpressure_hard_limit_raises() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus(backpressure=BackpressureController(warning_threshold=1, max_queue_size=1))

        await bus.publish(_reply_event(reply_id="reply_7"))
        with pytest.raises(BackpressureError):
            await bus.publish(_reply_event(reply_id="reply_8"))

    asyncio.run(_run())


def test_subscriber_failure_emits_warning_and_bus_keeps_running() -> None:
    async def _run() -> None:
        bus = PriorityPubSubBus()
        seen: list[str] = []
        warnings: list[str] = []

        async def boom(_event):
            raise RuntimeError("boom")

        async def ok(event):
            seen.append(event.payload.message.message_id or "")

        async def system_handler(event):
            warnings.append(event.payload.reason or "")

        bus.subscribe(consumer="bad", handler=boom, topic=Topic.INPUT_EVENT)
        bus.subscribe(consumer="good", handler=ok, topic=Topic.INPUT_EVENT)
        bus.subscribe(consumer="observer", handler=system_handler, topic=Topic.SYSTEM_SIGNAL)

        await bus.start()
        try:
            for message_id in ("msg_1", "msg_2"):
                event = build_envelope(
                    event_type=EventType.INPUT_TURN_RECEIVED,
                    source="input_normalizer",
                    target="broadcast",
                    payload=TurnInputPayload(
                        input_id=message_id,
                        input_mode="turn",
                        session_mode="turn_chat",
                        message=MessageRef(channel="cli", chat_id="direct", message_id=message_id),
                        user_text="hello",
                        metadata={"channel_kind": "chat"},
                    ),
                )
                await bus.publish(event)

            deadline = asyncio.get_running_loop().time() + 1.0
            while (len(seen) < 2 or len(warnings) < 2) and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)

            assert seen == ["msg_1", "msg_2"]
            assert len(warnings) == 2
            assert all("subscriber bad failed" in reason for reason in warnings)
            assert bus._pump is not None
            assert not bus._pump.done()
        finally:
            await bus.stop()

    asyncio.run(_run())
