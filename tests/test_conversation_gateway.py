from __future__ import annotations

import asyncio

from emoticorebot.adapters.conversation_gateway import ConversationGateway
from emoticorebot.adapters.outbound_dispatcher import OutboundDispatcher
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
        dispatcher=OutboundDispatcher(bus),
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
