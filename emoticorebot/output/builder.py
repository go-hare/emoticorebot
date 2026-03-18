"""Builders for output-layer reply events."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft, ReplyKind
from emoticorebot.protocol.topics import EventType


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class OutputEventBuilder:
    """Creates output events with stable delivery metadata."""

    def build(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        text: str,
        origin_message: MessageRef | None,
        related_task_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        kind: ReplyKind = "answer",
        safe_fallback: bool = False,
        reply_id: str | None = None,
        delivery_mode: str = "inline",
        stream_id: str | None = None,
        stream_state: str | None = None,
        stream_index: int | None = None,
        reply_metadata: dict[str, Any] | None = None,
        channel_override: str | None = None,
        chat_id_override: str | None = None,
    ) -> BusEnvelope[ReplyReadyPayload]:
        metadata = dict(reply_metadata or {})
        event_type = self._event_type(delivery_mode=delivery_mode, stream_state=stream_state)
        resolved_delivery_mode = self._delivery_mode(event_type=event_type, delivery_mode=delivery_mode)
        reply = ReplyDraft(
            reply_id=reply_id or _new_id("reply"),
            kind=kind,
            plain_text=text,
            safe_fallback=safe_fallback,
            reply_to_message_id=(origin_message.message_id if origin_message is not None else None),
            metadata=metadata,
        )
        return build_envelope(
            event_type=event_type,
            source="output_runtime",
            target="broadcast",
            session_id=session_id,
            turn_id=turn_id,
            task_id=related_task_id,
            correlation_id=correlation_id or related_task_id or turn_id,
            causation_id=causation_id,
            payload=ReplyReadyPayload(
                reply=reply,
                related_task_id=related_task_id,
                origin_message=origin_message,
                related_event_id=causation_id,
                channel_override=channel_override,
                chat_id_override=chat_id_override,
                delivery_mode=resolved_delivery_mode,
                stream_id=stream_id,
                stream_state=stream_state,
                stream_index=stream_index,
            ),
        )

    def reply(self, **kwargs: object) -> BusEnvelope[ReplyReadyPayload]:
        return self.build(**kwargs)

    def ask_user(self, **kwargs: object) -> BusEnvelope[ReplyReadyPayload]:
        return self.build(kind="ask_user", **kwargs)

    @staticmethod
    def _event_type(*, delivery_mode: str, stream_state: str | None) -> str:
        stream_state = str(stream_state or "").strip()
        if stream_state == "open":
            return EventType.OUTPUT_STREAM_OPEN
        if stream_state == "delta":
            return EventType.OUTPUT_STREAM_DELTA
        if stream_state in {"close", "superseded"}:
            return EventType.OUTPUT_STREAM_CLOSE
        delivery_mode = str(delivery_mode or "").strip()
        if delivery_mode == "push":
            return EventType.OUTPUT_PUSH_READY
        return EventType.OUTPUT_INLINE_READY

    @staticmethod
    def _delivery_mode(*, event_type: str, delivery_mode: str) -> str:
        delivery_mode = str(delivery_mode or "").strip()
        if event_type in {EventType.OUTPUT_STREAM_OPEN, EventType.OUTPUT_STREAM_DELTA, EventType.OUTPUT_STREAM_CLOSE}:
            return "stream"
        if event_type == EventType.OUTPUT_PUSH_READY:
            return "push"
        if delivery_mode in {"inline", "push", "stream"}:
            return delivery_mode
        return "inline"


__all__ = ["OutputEventBuilder"]
