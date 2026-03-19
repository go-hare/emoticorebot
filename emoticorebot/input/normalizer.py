"""Input normalization helpers that emit turn and stream input events."""

from __future__ import annotations

from typing import Any

from emoticorebot.protocol.contracts import ChannelKind, DeliveryMode, InputKind, SessionMode
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    InputSlots,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.task_models import ContentBlock, MessageRef
from emoticorebot.protocol.topics import EventType


class InputNormalizer:
    """Collapse channel-specific input into turn and stream business events."""

    def normalize_turn_input(
        self,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        input_kind: InputKind,
        channel_kind: ChannelKind,
        plain_text: str | None = None,
        content_blocks: list[ContentBlock] | None = None,
        attachments: list[str | ContentBlock] | None = None,
        metadata: dict[str, Any] | None = None,
        session_mode: SessionMode | None = None,
        barge_in: bool = False,
    ) -> BusEnvelope[TurnInputPayload]:
        user_text = str(plain_text or "").strip() or None
        payload_metadata = self._with_delivery_context(
            metadata,
            current_delivery_mode="inline",
            available_delivery_modes=["inline", "push"],
        )
        return build_envelope(
            event_type=EventType.INPUT_TURN_RECEIVED,
            source="input_normalizer",
            target="broadcast",
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=turn_id,
            payload=TurnInputPayload(
                input_id=turn_id,
                input_mode="turn",
                session_mode=session_mode or self._session_mode(channel_kind),
                channel_kind=channel_kind,
                input_kind=input_kind,
                barge_in=barge_in,
                message=MessageRef(
                    channel=channel,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    message_id=message_id,
                ),
                user_text=user_text,
                input_slots=InputSlots(),
                content_blocks=list(content_blocks or []),
                attachments=self._attachment_blocks(attachments),
                metadata=payload_metadata,
            ),
        )

    def normalize_text_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        content: str,
        attachments: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        channel_kind: ChannelKind = "chat",
        barge_in: bool = False,
    ) -> BusEnvelope[TurnInputPayload]:
        return self.normalize_turn_input(
            session_id=session_id,
            turn_id=turn_id,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            message_id=message_id,
            input_kind="text",
            channel_kind=channel_kind,
            plain_text=content,
            attachments=attachments,
            metadata=metadata,
            barge_in=barge_in,
        )

    def normalize_voice_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        transcript: str | None = None,
        content_blocks: list[ContentBlock] | None = None,
        attachments: list[str | ContentBlock] | None = None,
        metadata: dict[str, Any] | None = None,
        channel_kind: ChannelKind = "voice",
        barge_in: bool = False,
    ) -> BusEnvelope[TurnInputPayload]:
        return self.normalize_turn_input(
            session_id=session_id,
            turn_id=turn_id,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            message_id=message_id,
            input_kind="voice",
            channel_kind=channel_kind,
            plain_text=transcript,
            content_blocks=content_blocks,
            attachments=attachments,
            metadata=metadata,
            barge_in=barge_in,
        )

    def normalize_video_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        plain_text: str | None = None,
        content_blocks: list[ContentBlock] | None = None,
        attachments: list[str | ContentBlock] | None = None,
        metadata: dict[str, Any] | None = None,
        channel_kind: ChannelKind = "video",
        input_kind: InputKind = "multimodal",
        barge_in: bool = False,
    ) -> BusEnvelope[TurnInputPayload]:
        return self.normalize_turn_input(
            session_id=session_id,
            turn_id=turn_id,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            message_id=message_id,
            input_kind=input_kind,
            channel_kind=channel_kind,
            plain_text=plain_text,
            content_blocks=content_blocks,
            attachments=attachments,
            metadata=metadata,
            barge_in=barge_in,
        )

    def normalize_stream_start(
        self,
        *,
        session_id: str,
        stream_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        channel_kind: ChannelKind,
        input_kind: InputKind = "voice",
        metadata: dict[str, Any] | None = None,
        session_mode: SessionMode | None = None,
    ) -> BusEnvelope[StreamStartPayload]:
        resolved_session_mode = session_mode or self._session_mode(channel_kind)
        payload_metadata = self._with_delivery_context(
            metadata,
            current_delivery_mode="stream",
            available_delivery_modes=["stream", "inline", "push"],
        )
        payload_metadata.setdefault("channel_kind", channel_kind)
        payload_metadata.setdefault("input_kind", input_kind)
        payload_metadata.setdefault("session_mode", resolved_session_mode)
        return build_envelope(
            event_type=EventType.INPUT_STREAM_STARTED,
            source="input_normalizer",
            target="broadcast",
            session_id=session_id,
            correlation_id=stream_id,
            payload=StreamStartPayload(
                session_mode=resolved_session_mode,
                stream_id=stream_id,
                message=MessageRef(
                    channel=channel,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    message_id=message_id,
                ),
                metadata=payload_metadata,
            ),
        )

    def normalize_stream_chunk(
        self,
        *,
        session_id: str,
        stream_id: str,
        chunk_index: int,
        chunk_text: str,
        is_commit_point: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> BusEnvelope[StreamChunkPayload]:
        return build_envelope(
            event_type=EventType.INPUT_STREAM_CHUNK,
            source="input_normalizer",
            target="broadcast",
            session_id=session_id,
            correlation_id=stream_id,
            payload=StreamChunkPayload(
                stream_id=stream_id,
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                is_commit_point=is_commit_point,
                metadata=dict(metadata or {}),
            ),
        )

    def normalize_stream_commit(
        self,
        *,
        session_id: str,
        turn_id: str,
        stream_id: str,
        committed_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> BusEnvelope[StreamCommitPayload]:
        return build_envelope(
            event_type=EventType.INPUT_STREAM_COMMITTED,
            source="input_normalizer",
            target="broadcast",
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=turn_id,
            payload=StreamCommitPayload(
                stream_id=stream_id,
                committed_text=committed_text,
                metadata=dict(metadata or {}),
            ),
        )

    def normalize_stream_interrupted(
        self,
        *,
        session_id: str,
        stream_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BusEnvelope[StreamInterruptedPayload]:
        return build_envelope(
            event_type=EventType.INPUT_STREAM_INTERRUPTED,
            source="input_normalizer",
            target="broadcast",
            session_id=session_id,
            correlation_id=stream_id,
            payload=StreamInterruptedPayload(
                stream_id=stream_id,
                reason=reason,
                metadata=dict(metadata or {}),
            ),
        )

    @staticmethod
    def _session_mode(channel_kind: ChannelKind) -> SessionMode:
        return "turn_chat" if str(channel_kind or "").strip() == "chat" else "realtime_chat"

    @staticmethod
    def _attachment_blocks(attachments: list[str | ContentBlock] | None) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for item in list(attachments or []):
            if isinstance(item, ContentBlock):
                blocks.append(item)
                continue
            path = str(item or "").strip()
            if not path:
                continue
            blocks.append(ContentBlock(type="file", path=path, name=path.rsplit("/", 1)[-1]))
        return blocks

    @classmethod
    def _with_delivery_context(
        cls,
        metadata: dict[str, Any] | None,
        *,
        current_delivery_mode: DeliveryMode,
        available_delivery_modes: list[DeliveryMode],
    ) -> dict[str, Any]:
        payload_metadata = dict(metadata or {})
        resolved_current = str(payload_metadata.get("current_delivery_mode", "") or "").strip()
        if resolved_current not in {"inline", "push", "stream"}:
            resolved_current = current_delivery_mode
        payload_metadata["current_delivery_mode"] = resolved_current

        resolved_available: list[str] = []
        raw_available = payload_metadata.get("available_delivery_modes")
        if isinstance(raw_available, list):
            for item in raw_available:
                value = str(item or "").strip()
                if value in {"inline", "push", "stream"} and value not in resolved_available:
                    resolved_available.append(value)
        if not resolved_available:
            resolved_available = [mode for mode in available_delivery_modes if mode not in resolved_available]
        if resolved_current not in resolved_available:
            resolved_available.insert(0, resolved_current)
        payload_metadata["available_delivery_modes"] = resolved_available
        return payload_metadata


__all__ = ["InputNormalizer"]
