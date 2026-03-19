"""Output runtime that converts left-brain events into delivery-layer events."""

from __future__ import annotations

from typing import Any

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import (
    LeftFollowupReadyPayload,
    LeftReplyReadyPayload,
    LeftStreamDeltaPayload,
    OutputReadyPayloadBase,
    ReplyBlockedPayload,
)
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.safety.guard import SafetyGuard

from .builder import OutputEventBuilder


class OutputRuntime:
    """Owns left-event to output-event conversion and reply safety filtering."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        reply_guard: SafetyGuard | None = None,
        builder: OutputEventBuilder | None = None,
    ) -> None:
        self._bus = bus
        self._reply_guard = reply_guard or SafetyGuard()
        self._builder = builder or OutputEventBuilder()

    def register(self) -> None:
        self._bus.subscribe(consumer="output_runtime", event_type=EventType.LEFT_EVENT_REPLY_READY, handler=self._on_left_reply_ready)
        self._bus.subscribe(
            consumer="output_runtime",
            event_type=EventType.LEFT_EVENT_STREAM_DELTA_READY,
            handler=self._on_left_stream_delta_ready,
        )
        self._bus.subscribe(
            consumer="output_runtime",
            event_type=EventType.LEFT_EVENT_FOLLOWUP_READY,
            handler=self._on_left_followup_ready,
        )

    async def _on_left_reply_ready(self, event: BusEnvelope[LeftReplyReadyPayload]) -> None:
        if self._should_skip_reply_output(event.payload.metadata):
            return
        output_event = self._build_from_left_reply(event)
        await self._publish_guarded(output_event)

    async def _on_left_stream_delta_ready(self, event: BusEnvelope[LeftStreamDeltaPayload]) -> None:
        output_event = self._builder.reply(
            session_id=event.session_id or "",
            turn_id=event.turn_id,
            text=event.payload.delta_text,
            origin_message=event.payload.origin_message,
            related_task_id=event.task_id,
            causation_id=event.event_id,
            correlation_id=event.correlation_id or event.task_id or event.turn_id,
            kind="answer",
            reply_id=f"{event.payload.stream_id}_{event.payload.stream_index or 'chunk'}",
            delivery_mode="stream",
            stream_id=event.payload.stream_id,
            stream_state=event.payload.stream_state,
            stream_index=event.payload.stream_index,
            reply_metadata=self._reply_metadata(event.payload.metadata),
        )
        await self._publish_guarded(output_event)

    async def _on_left_followup_ready(self, event: BusEnvelope[LeftFollowupReadyPayload]) -> None:
        if self._should_skip_reply_output(event.payload.metadata):
            return
        delivery_target = event.payload.delivery_target
        stream_id = self._followup_stream_id(event.payload) if delivery_target.delivery_mode == "stream" else None
        stream_state = "close" if delivery_target.delivery_mode == "stream" else None
        output_event = self._build_reply_event(
            session_id=event.session_id or "",
            turn_id=event.turn_id,
            text=event.payload.reply_text,
            origin_message=event.payload.origin_message,
            related_task_id=event.payload.related_task_id or event.task_id,
            causation_id=event.event_id,
            correlation_id=event.correlation_id or event.task_id or event.payload.job_id,
            reply_kind=event.payload.reply_kind,
            delivery_mode=delivery_target.delivery_mode,
            reply_metadata=dict(event.payload.metadata or {}),
            stream_id=stream_id,
            stream_state=stream_state,
            channel_override=delivery_target.channel,
            chat_id_override=delivery_target.chat_id,
        )
        await self._publish_guarded(output_event)

    def _build_from_left_reply(self, event: BusEnvelope[LeftReplyReadyPayload]) -> BusEnvelope[OutputReadyPayloadBase]:
        delivery_target = event.payload.delivery_target
        delivery_mode = self._left_reply_delivery_mode(event.payload)
        stream_id = event.payload.stream_id
        stream_state = event.payload.stream_state
        if delivery_mode == "stream" and stream_state is None:
            stream_id = stream_id or self._left_reply_stream_id(event.payload, turn_id=event.turn_id)
            stream_state = "close"
        return self._build_reply_event(
            session_id=event.session_id or "",
            turn_id=event.turn_id,
            text=event.payload.reply_text,
            origin_message=event.payload.origin_message,
            related_task_id=event.payload.related_task_id or event.task_id,
            causation_id=event.event_id,
            correlation_id=event.correlation_id or event.task_id or event.turn_id,
            reply_kind=event.payload.reply_kind,
            delivery_mode=delivery_mode,
            reply_metadata=dict(event.payload.metadata or {}),
            stream_id=stream_id,
            stream_state=stream_state,
            stream_index=event.payload.stream_index,
            channel_override=delivery_target.channel,
            chat_id_override=delivery_target.chat_id,
        )

    def _build_reply_event(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        text: str,
        origin_message: MessageRef | None,
        related_task_id: str | None,
        causation_id: str | None,
        correlation_id: str | None,
        reply_kind: str,
        delivery_mode: str,
        reply_metadata: dict[str, Any] | None,
        stream_id: str | None = None,
        stream_state: str | None = None,
        stream_index: int | None = None,
        channel_override: str | None = None,
        chat_id_override: str | None = None,
        safe_fallback: bool = False,
    ) -> BusEnvelope[OutputReadyPayloadBase]:
        metadata = self._reply_metadata(reply_metadata)
        return self._builder.reply(
            session_id=session_id,
            turn_id=turn_id,
            text=text,
            origin_message=origin_message,
            related_task_id=related_task_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            kind="status" if reply_kind == "status" else ("safety_fallback" if safe_fallback else "answer"),
            safe_fallback=safe_fallback,
            delivery_mode=delivery_mode,
            stream_id=stream_id,
            stream_state=stream_state,
            stream_index=stream_index,
            reply_metadata=metadata,
            channel_override=channel_override,
            chat_id_override=chat_id_override,
        )

    async def _publish_guarded(self, event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        guarded = self._reply_guard.guard_reply_event(event)
        if guarded.event is not None:
            await self._bus.publish(guarded.event)
            return
        if guarded.blocked is None or event.payload.content.safe_fallback:
            return
        await self._bus.publish(self._build_safe_fallback(event, guarded.blocked))

    def _build_safe_fallback(
        self,
        event: BusEnvelope[OutputReadyPayloadBase],
        blocked: ReplyBlockedPayload,
    ) -> BusEnvelope[OutputReadyPayloadBase]:
        return self._build_reply_event(
            session_id=event.session_id or "",
            turn_id=event.turn_id,
            text=self._safe_fallback_text(blocked),
            origin_message=event.payload.origin_message,
            related_task_id=event.payload.related_task_id,
            causation_id=event.causation_id or event.event_id,
            correlation_id=event.correlation_id or event.task_id or event.turn_id,
            reply_kind="answer",
            delivery_mode=self._fallback_delivery_mode(event),
            reply_metadata=self._fallback_reply_metadata(event),
            channel_override=event.payload.delivery_target.channel,
            chat_id_override=event.payload.delivery_target.chat_id,
            safe_fallback=True,
        )

    @staticmethod
    def _safe_fallback_text(blocked: ReplyBlockedPayload) -> str:
        hint = blocked.redaction_hint or "请去掉敏感信息后再试。"
        return f"这条内容我不能直接发出。{hint}"

    @staticmethod
    def _should_skip_reply_output(metadata: dict[str, Any] | None) -> bool:
        return bool((metadata or {}).get("suppress_output"))

    @staticmethod
    def _left_reply_delivery_mode(payload: LeftReplyReadyPayload) -> str:
        if payload.stream_state is not None:
            return "stream"
        mode = str(payload.delivery_target.delivery_mode or "").strip()
        return mode if mode in {"inline", "push", "stream"} else "inline"

    @staticmethod
    def _reply_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return dict(extra or {})

    @staticmethod
    def _left_reply_stream_id(payload: LeftReplyReadyPayload, *, turn_id: str | None) -> str:
        return str(payload.stream_id or payload.request_id or turn_id or "stream_reply").strip() or "stream_reply"

    @staticmethod
    def _followup_stream_id(payload: LeftFollowupReadyPayload) -> str:
        metadata = dict(payload.metadata or {})
        stream_id = str(metadata.get("stream_id", "") or "").strip()
        if stream_id:
            return stream_id
        return f"stream_followup_{payload.job_id}"

    @staticmethod
    def _fallback_delivery_mode(event: BusEnvelope[OutputReadyPayloadBase]) -> str:
        if getattr(event.payload, "stream_state", None) is not None:
            return "inline"
        delivery_mode = str(event.payload.delivery_target.delivery_mode or "").strip()
        return delivery_mode if delivery_mode in {"inline", "push"} else "inline"

    @staticmethod
    def _fallback_reply_metadata(event: BusEnvelope[OutputReadyPayloadBase]) -> dict[str, Any]:
        metadata = dict(event.payload.content.metadata or {})
        fallback: dict[str, Any] = {}
        if metadata.get("suppress_delivery"):
            fallback["suppress_delivery"] = True
        return fallback


__all__ = ["OutputRuntime"]
