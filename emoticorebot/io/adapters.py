"""Small helpers for IO adapter integration."""

from __future__ import annotations

from typing import Any

from emoticorebot.io.models import InputSlots, NormalizedInput
from emoticorebot.protocol.contracts import ChannelKind, InputKind
from emoticorebot.protocol.task_models import ContentBlock


def build_turn_input(
    *,
    session_id: str,
    turn_id: str,
    input_kind: InputKind,
    channel_kind: ChannelKind,
    plain_text: str | None = None,
    content_blocks: list[ContentBlock] | None = None,
    attachments: list[ContentBlock] | None = None,
    metadata: dict[str, Any] | None = None,
    barge_in: bool = False,
) -> NormalizedInput:
    user_text = str(plain_text).strip() if plain_text is not None else None
    slots = InputSlots(user=user_text or "", task="")
    return NormalizedInput(
        session_id=session_id,
        turn_id=turn_id,
        user_text=user_text,
        input_slots=slots,
        channel_kind=channel_kind,
        input_kind=input_kind,
        content_blocks=list(content_blocks or []),
        attachments=list(attachments or []),
        barge_in=barge_in,
        metadata=dict(metadata or {}),
    )


def build_text_input(
    *,
    session_id: str,
    turn_id: str,
    plain_text: str | None = None,
    attachments: list[ContentBlock] | None = None,
    metadata: dict[str, Any] | None = None,
    channel_kind: ChannelKind = "chat",
    barge_in: bool = False,
) -> NormalizedInput:
    return build_turn_input(
        session_id=session_id,
        turn_id=turn_id,
        plain_text=plain_text,
        attachments=attachments,
        channel_kind=channel_kind,
        input_kind="text",
        metadata=metadata,
        barge_in=barge_in,
    )


def build_voice_input(
    *,
    session_id: str,
    turn_id: str,
    transcript: str | None = None,
    content_blocks: list[ContentBlock] | None = None,
    attachments: list[ContentBlock] | None = None,
    metadata: dict[str, Any] | None = None,
    channel_kind: ChannelKind = "voice",
    barge_in: bool = False,
) -> NormalizedInput:
    return build_turn_input(
        session_id=session_id,
        turn_id=turn_id,
        input_kind="voice",
        channel_kind=channel_kind,
        plain_text=transcript,
        content_blocks=content_blocks,
        attachments=attachments,
        metadata=metadata,
        barge_in=barge_in,
    )


def build_video_input(
    *,
    session_id: str,
    turn_id: str,
    plain_text: str | None = None,
    content_blocks: list[ContentBlock] | None = None,
    attachments: list[ContentBlock] | None = None,
    metadata: dict[str, Any] | None = None,
    channel_kind: ChannelKind = "video",
    input_kind: InputKind = "multimodal",
    barge_in: bool = False,
) -> NormalizedInput:
    return build_turn_input(
        session_id=session_id,
        turn_id=turn_id,
        input_kind=input_kind,
        channel_kind=channel_kind,
        plain_text=plain_text,
        content_blocks=content_blocks,
        attachments=attachments,
        metadata=metadata,
        barge_in=barge_in,
    )


__all__ = ["build_turn_input", "build_text_input", "build_voice_input", "build_video_input"]
