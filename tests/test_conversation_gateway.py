from __future__ import annotations

import asyncio

from emoticorebot.adapters.conversation_gateway import ConversationGateway
from emoticorebot.runtime.transport_bus import InboundMessage, OutboundMessage, TransportBus


async def _exercise_gateway_dispatch_does_not_republish_processor_response() -> None:
    bus = TransportBus()
    seen: list[str] = []

    async def _message_processor(msg: InboundMessage, _session_key: str | None, _on_progress):
        seen.append(msg.content)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="hello",
            reply_to=str(msg.metadata.get("message_id", "") or None),
        )

    gateway = ConversationGateway(
        bus=bus,
        message_processor=_message_processor,
    )

    await gateway.dispatch(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="ping",
            metadata={"message_id": "msg_1"},
        )
    )

    assert seen == ["ping"]
    assert bus.outbound_size == 0


def test_conversation_gateway_dispatch_does_not_publish_duplicate_outbound() -> None:
    asyncio.run(_exercise_gateway_dispatch_does_not_republish_processor_response())


async def _exercise_gateway_interrupts_active_session_dispatch() -> None:
    bus = TransportBus()
    slow_started = asyncio.Event()
    slow_cancelled = asyncio.Event()
    fast_done = asyncio.Event()
    seen: list[str] = []

    async def _message_processor(msg: InboundMessage, _session_key: str | None, _on_progress):
        seen.append(msg.content)
        if msg.content == "slow":
            slow_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                slow_cancelled.set()
                raise
        fast_done.set()
        return None

    gateway = ConversationGateway(
        bus=bus,
        message_processor=_message_processor,
    )

    gateway.spawn_dispatch(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="slow",
            metadata={"message_id": "msg_slow"},
        )
    )
    await asyncio.wait_for(slow_started.wait(), timeout=1.0)

    gateway.spawn_dispatch(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="fast",
            metadata={"message_id": "msg_fast"},
        )
    )

    await asyncio.wait_for(slow_cancelled.wait(), timeout=1.0)
    await asyncio.wait_for(fast_done.wait(), timeout=1.0)

    assert seen == ["slow", "fast"]


def test_conversation_gateway_interrupts_active_session_dispatch() -> None:
    asyncio.run(_exercise_gateway_interrupts_active_session_dispatch())
