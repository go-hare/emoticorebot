"""Input normalization helpers that emit turn and stream input events."""

from __future__ import annotations

import re
from typing import Any

from emoticorebot.protocol.contracts import ChannelKind, InputKind, SessionMode
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

_USER_SLOT_RE = re.compile(r"#+\s*user\s*#+", re.IGNORECASE)
_TASK_SLOT_RE = re.compile(r"#+\s*task\s*#+", re.IGNORECASE)


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
        user_text, input_slots = self._parse_input_slots(plain_text)
        payload_metadata = dict(metadata or {})
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
                input_slots=input_slots,
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
        payload_metadata = dict(metadata or {})
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
    def _parse_input_slots(plain_text: str | None) -> tuple[str | None, InputSlots]:
        text = str(plain_text or "").strip()
        if not text:
            return None, InputSlots()

        user_match = _USER_SLOT_RE.search(text)
        task_match = _TASK_SLOT_RE.search(text)
        user_slot = ""
        task_slot = ""

        if user_match and task_match and user_match.start() < task_match.start():
            user_slot = text[user_match.end() : task_match.start()].strip()
            task_slot = text[task_match.end() :].strip()
        elif task_match and not user_match:
            user_slot = text[: task_match.start()].strip()
            task_slot = text[task_match.end() :].strip()
        elif user_match:
            user_slot = text[user_match.end() :].strip()
        else:
            user_slot = text

        normalized_user_text = user_slot or None
        return normalized_user_text, InputSlots(user=user_slot, task=task_slot)

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


__all__ = ["InputNormalizer"]
