"""Output runtime that converts brain events into delivery-layer events."""

from __future__ import annotations

from typing import Any

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import (
    BrainReplyReadyPayload,
    BrainStreamDeltaPayload,
    OutputReadyPayloadBase,
)
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.safety.guard import SafetyGuard

from .builder import OutputEventBuilder


class OutputRuntime:
    """Owns brain-event to output-event conversion and reply safety filtering."""

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
        self._bus.subscribe(consumer="output_runtime", event_type=EventType.BRAIN_EVENT_REPLY_READY, handler=self._on_brain_reply_ready)
        self._bus.subscribe(
            consumer="output_runtime",
            event_type=EventType.BRAIN_EVENT_STREAM_DELTA_READY,
            handler=self._on_brain_stream_delta_ready,
        )

    async def _on_brain_reply_ready(self, event: BusEnvelope[BrainReplyReadyPayload]) -> None:
        if self._should_skip_reply_output(event.payload.metadata):
            return
        output_event = self._build_from_brain_reply(event)
        await self._publish_guarded(output_event)

    async def _on_brain_stream_delta_ready(self, event: BusEnvelope[BrainStreamDeltaPayload]) -> None:
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

    def _build_from_brain_reply(self, event: BusEnvelope[BrainReplyReadyPayload]) -> BusEnvelope[OutputReadyPayloadBase]:
        delivery_target = event.payload.delivery_target
        delivery_mode = self._brain_reply_delivery_mode(event.payload)
        stream_id = event.payload.stream_id
        stream_state = event.payload.stream_state
        if delivery_mode == "stream" and stream_state is None:
            stream_id = stream_id or self._brain_reply_stream_id(event.payload, turn_id=event.turn_id)
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
            kind="status" if reply_kind == "status" else "answer",
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
        return

    @staticmethod
    def _should_skip_reply_output(metadata: dict[str, Any] | None) -> bool:
        return bool((metadata or {}).get("suppress_output"))

    @staticmethod
    def _brain_reply_delivery_mode(payload: BrainReplyReadyPayload) -> str:
        if payload.stream_state is not None:
            return "stream"
        mode = str(payload.delivery_target.delivery_mode or "").strip()
        return mode if mode in {"inline", "push", "stream"} else "inline"

    @staticmethod
    def _reply_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return dict(extra or {})

    @staticmethod
    def _brain_reply_stream_id(payload: BrainReplyReadyPayload, *, turn_id: str | None) -> str:
        return str(payload.stream_id or payload.request_id or turn_id or "stream_reply").strip() or "stream_reply"

__all__ = ["OutputRuntime"]
