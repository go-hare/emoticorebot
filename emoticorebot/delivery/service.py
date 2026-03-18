"""Delivery service for approved and redacted replies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import DeliveryFailedPayload, RepliedPayload, ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef, ProtocolModel
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import OutboundMessage, TransportBus


class DeliveryService:
    """Bridges approved replies to the transport-facing outbound queue."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        transport: TransportBus | None = None,
        should_deliver: Callable[[BusEnvelope[ReplyReadyPayload]], bool] | None = None,
    ) -> None:
        self._bus = bus
        self._transport = transport
        self._should_deliver = should_deliver

    def register(self) -> None:
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_INLINE_READY, handler=self._deliver)
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_PUSH_READY, handler=self._deliver)
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_STREAM_OPEN, handler=self._deliver)
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_STREAM_DELTA, handler=self._deliver)
        self._bus.subscribe(consumer="delivery", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=self._deliver)

    async def _deliver(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        payload = event.payload
        reply_metadata = dict(payload.reply.metadata or {})
        stream_state = self._stream_state(event.event_type, reply_metadata)
        origin = payload.origin_message or MessageRef()
        channel = payload.channel_override or origin.channel
        chat_id = payload.chat_id_override or origin.chat_id
        if self._should_deliver is not None and not self._should_deliver(event):
            if self._is_stream(event.event_type, reply_metadata):
                await self._publish_superseded(
                    event,
                    channel=channel,
                    chat_id=chat_id,
                    reply_metadata=reply_metadata,
                )
                return
            await self._publish_failed(event, reason="stale_reply_dropped")
            return
        if self._is_suppressed(reply_metadata):
            if stream_state in {"open", "delta", "superseded"}:
                return
            delivered_at = self._utc_now()
            await self._publish_replied(
                event,
                channel=channel,
                chat_id=chat_id,
                delivery_message_id=self._suppressed_message_id(payload.reply.reply_id),
                delivery_mode="suppressed",
                delivered_at=delivered_at,
                reply_to_message_id=payload.reply.reply_to_message_id or origin.message_id,
            )
            return
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
                "reply_kind": payload.reply.kind,
                "session_id": event.session_id,
                "task_id": event.task_id,
                "_stream": bool(reply_metadata.get("stream_id")) or stream_state in {"open", "delta", "close"},
                "_stream_id": str(reply_metadata.get("stream_id", "") or "").strip(),
                "_stream_state": stream_state,
                "_stream_index": reply_metadata.get("stream_index"),
            },
        )
        try:
            await self._transport.publish_outbound(outbound)
        except Exception:
            await self._publish_failed(event, reason="delivery_transport_error", retryable=True)
            return

        if stream_state in {"open", "delta", "superseded"}:
            return

        delivered_at = self._utc_now()
        await self._publish_replied(
            event,
            channel=channel,
            chat_id=chat_id,
            delivery_message_id=delivery_message_id,
            delivery_mode=payload.delivery_mode,
            delivered_at=delivered_at,
            reply_to_message_id=payload.reply.reply_to_message_id or origin.message_id,
        )

    async def _publish_replied(
        self,
        event: BusEnvelope[ReplyReadyPayload],
        *,
        channel: str | None,
        chat_id: str | None,
        delivery_message_id: str,
        delivery_mode: str,
        delivered_at: str,
        reply_to_message_id: str | None,
    ) -> None:
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
                    reply_id=event.payload.reply.reply_id,
                    delivery_message=MessageRef(
                        channel=channel,
                        chat_id=chat_id,
                        message_id=delivery_message_id,
                        reply_to_message_id=reply_to_message_id,
                        timestamp=delivered_at,
                    ),
                    delivery_mode=delivery_mode,
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
    def _suppressed_message_id(reply_id: str) -> str:
        return f"suppressed_{reply_id}"

    @staticmethod
    def _is_suppressed(reply_metadata: dict[str, object]) -> bool:
        return bool(reply_metadata.get("suppress_delivery"))

    @staticmethod
    def _is_stream(event_type: str, reply_metadata: dict[str, object]) -> bool:
        if str(event_type) in {
            str(EventType.OUTPUT_STREAM_OPEN),
            str(EventType.OUTPUT_STREAM_DELTA),
            str(EventType.OUTPUT_STREAM_CLOSE),
        }:
            return True
        return bool(str(reply_metadata.get("stream_id", "") or "").strip())

    @staticmethod
    def _stream_state(event_type: str, reply_metadata: dict[str, object]) -> str:
        if str(event_type) == str(EventType.OUTPUT_STREAM_OPEN):
            return "open"
        if str(event_type) == str(EventType.OUTPUT_STREAM_DELTA):
            return "delta"
        if str(event_type) == str(EventType.OUTPUT_STREAM_CLOSE):
            return "close"
        stream_state = str(reply_metadata.get("stream_state", "") or "").strip()
        if stream_state == "final":
            return "close"
        return stream_state

    async def _publish_superseded(
        self,
        event: BusEnvelope[ReplyReadyPayload],
        *,
        channel: str | None,
        chat_id: str | None,
        reply_metadata: dict[str, object],
    ) -> None:
        if self._transport is None or not channel or not chat_id:
            return
        stream_id = str(reply_metadata.get("stream_id", "") or "").strip()
        if not stream_id:
            return
        await self._transport.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content="",
                message_id=self._delivery_message_id(event.payload.reply.reply_id),
                reply_to=event.payload.reply.reply_to_message_id or (event.payload.origin_message.message_id if event.payload.origin_message else None),
                metadata={
                    "reply_id": event.payload.reply.reply_id,
                    "reply_kind": event.payload.reply.kind,
                    "session_id": event.session_id,
                    "task_id": event.task_id,
                    "_stream": True,
                    "_stream_id": stream_id,
                    "_stream_state": "superseded",
                    "_stream_index": reply_metadata.get("stream_index"),
                },
            )
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["DeliveryService"]
