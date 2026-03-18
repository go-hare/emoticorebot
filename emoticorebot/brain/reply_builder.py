"""Reply command builders for the executive brain."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft, ReplyKind
from emoticorebot.protocol.topics import EventType


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ReplyBuilder:
    """Creates output inline/push/stream envelopes with stable protocol fields."""

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
        reply_metadata: dict[str, Any] | None = None,
    ) -> BusEnvelope[ReplyReadyPayload]:
        metadata = dict(reply_metadata or {})
        event_type = self._event_type(metadata)
        delivery_mode = self._delivery_mode(event_type=event_type, metadata=metadata)
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
            source="brain",
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
                delivery_mode=delivery_mode,
            ),
        )

    def reply(self, **kwargs: object) -> BusEnvelope[ReplyReadyPayload]:
        return self.build(**kwargs)

    def ask_user(self, **kwargs: object) -> BusEnvelope[ReplyReadyPayload]:
        return self.build(kind="ask_user", **kwargs)

    @staticmethod
    def _event_type(metadata: dict[str, Any]) -> str:
        stream_state = str(metadata.get("stream_state", "") or "").strip()
        if stream_state in {"open"}:
            return EventType.OUTPUT_STREAM_OPEN
        if stream_state in {"delta"}:
            return EventType.OUTPUT_STREAM_DELTA
        if stream_state in {"close", "final"}:
            return EventType.OUTPUT_STREAM_CLOSE
        delivery_mode = str(metadata.get("delivery_mode", "") or "").strip()
        if delivery_mode == "push" or str(metadata.get("front_origin", "") or "").strip() == "task":
            return EventType.OUTPUT_PUSH_READY
        return EventType.OUTPUT_INLINE_READY

    @staticmethod
    def _delivery_mode(*, event_type: str, metadata: dict[str, Any]) -> str:
        delivery_mode = str(metadata.get("delivery_mode", "") or "").strip()
        if delivery_mode in {"inline", "push", "stream"}:
            return delivery_mode
        if event_type == EventType.OUTPUT_PUSH_READY:
            return "push"
        if event_type in {EventType.OUTPUT_STREAM_OPEN, EventType.OUTPUT_STREAM_DELTA, EventType.OUTPUT_STREAM_CLOSE}:
            return "stream"
        return "inline"


__all__ = ["ReplyBuilder"]
