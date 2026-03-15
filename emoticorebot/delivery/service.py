"""Delivery service for approved and redacted replies."""

from __future__ import annotations

from datetime import UTC, datetime

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import DeliveryFailedPayload, RepliedPayload, ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef, ProtocolModel
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import OutboundMessage, TransportBus


class DeliveryService:
    """Bridges approved replies to the transport-facing outbound queue."""

    def __init__(self, *, bus: PriorityPubSubBus, transport: TransportBus | None = None) -> None:
        self._bus = bus
        self._transport = transport

    def register(self) -> None:
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_REPLY_APPROVED, handler=self._deliver)
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=self._deliver)

    async def _deliver(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        payload = event.payload
        origin = payload.origin_message or MessageRef()
        channel = payload.channel_override or origin.channel
        chat_id = payload.chat_id_override or origin.chat_id
        if not channel or not chat_id:
            await self._publish_failed(event, reason="missing_delivery_route")
            return
        if self._transport is None:
            await self._publish_failed(event, reason="delivery_transport_unavailable")
            return

        delivery_message_id = self._delivery_message_id(payload.reply.reply_id)
        content = self._render_text(payload)
        outbound = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            message_id=delivery_message_id,
            reply_to=payload.reply.reply_to_message_id or origin.message_id,
            metadata={
                "reply_id": payload.reply.reply_id,
                "session_id": event.session_id,
                "task_id": event.task_id,
            },
        )
        try:
            await self._transport.publish_outbound(outbound)
        except Exception:
            await self._publish_failed(event, reason="delivery_transport_error", retryable=True)
            return

        delivered_at = self._utc_now()

        await self._bus.publish(
            build_envelope(
                event_type=EventType.OUTPUT_REPLIED,
                source="delivery",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=RepliedPayload(
                    reply_id=payload.reply.reply_id,
                    delivery_message=MessageRef(
                        channel=channel,
                        chat_id=chat_id,
                        message_id=delivery_message_id,
                        reply_to_message_id=payload.reply.reply_to_message_id or origin.message_id,
                        timestamp=delivered_at,
                    ),
                    delivery_mode=payload.delivery_mode or "chat",
                    delivered_at=delivered_at,
                ),
            )
        )

    async def _publish_failed(
        self,
        event: BusEnvelope[ProtocolModel],
        *,
        reason: str,
        retryable: bool = False,
    ) -> None:
        payload = event.payload
        reply = getattr(payload, "reply", None)
        reply_id = getattr(reply, "reply_id", "")
        await self._bus.publish(
            build_envelope(
                event_type=EventType.OUTPUT_DELIVERY_FAILED,
                source="delivery",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=DeliveryFailedPayload(reply_id=reply_id, reason=reason, retryable=retryable),
            )
        )

    @staticmethod
    def _render_text(payload: ReplyReadyPayload) -> str:
        if payload.reply.plain_text:
            return payload.reply.plain_text
        parts = [block.text for block in payload.reply.content_blocks if block.type == "text" and block.text]
        return "\n".join(parts).strip()

    @staticmethod
    def _delivery_message_id(reply_id: str) -> str:
        return f"delivery_{reply_id}"

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["DeliveryService"]
