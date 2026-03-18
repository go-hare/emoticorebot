"""Normalized input models used by the refactor-aligned IO layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from emoticorebot.protocol.contracts import ChannelKind, InputKind, InputMode, SessionMode
from emoticorebot.protocol.task_models import ContentBlock


@dataclass(slots=True)
class InputSlots:
    user: str = ""
    task: str = ""


@dataclass(slots=True)
class NormalizedInput:
    session_id: str
    turn_id: str
    input_mode: InputMode = "turn"
    session_mode: SessionMode = "turn_chat"
    user_text: str | None = None
    input_slots: InputSlots = field(default_factory=InputSlots)
    channel_kind: ChannelKind = "chat"
    input_kind: InputKind = "text"
    content_blocks: list[ContentBlock] = field(default_factory=list)
    attachments: list[ContentBlock] = field(default_factory=list)
    barge_in: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["InputSlots", "NormalizedInput"]
