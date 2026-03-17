"""Input normalization helpers that emit stable input events."""

from __future__ import annotations

from typing import Any

from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import StableInputPayload
from emoticorebot.protocol.task_models import ContentBlock, MessageRef
from emoticorebot.protocol.topics import EventType


class InputNormalizer:
    """Collapse channel-specific input into one stable business event."""

    def normalize_stable_input(
        self,
        *,
        session_id: str,
        turn_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        input_kind: str,
        channel_kind: str,
        plain_text: str | None = None,
        content_blocks: list[ContentBlock] | None = None,
        attachments: list[str | ContentBlock] | None = None,
        metadata: dict[str, Any] | None = None,
        barge_in: bool = False,
    ) -> BusEnvelope[StableInputPayload]:
        normalized_text = str(plain_text or "").strip() or None
        payload_metadata = dict(metadata or {})
        payload_metadata.setdefault("channel_kind", channel_kind)
        payload_metadata.setdefault("input_kind", input_kind)
        return build_envelope(
            event_type=EventType.INPUT_STABLE,
            source="input_normalizer",
            target="broadcast",
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=turn_id,
            payload=StableInputPayload(
                input_id=turn_id,
                input_kind=input_kind,
                channel_kind=channel_kind,
                barge_in=barge_in,
                message=MessageRef(
                    channel=channel,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    message_id=message_id,
                ),
                plain_text=normalized_text,
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
        channel_kind: str = "chat",
        barge_in: bool = False,
    ) -> BusEnvelope[StableInputPayload]:
        return self.normalize_stable_input(
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
        channel_kind: str = "voice",
        barge_in: bool = False,
    ) -> BusEnvelope[StableInputPayload]:
        return self.normalize_stable_input(
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
        channel_kind: str = "video",
        input_kind: str = "multimodal",
        barge_in: bool = False,
    ) -> BusEnvelope[StableInputPayload]:
        return self.normalize_stable_input(
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
