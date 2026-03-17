"""Normalized input models used by the refactor-aligned IO layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from emoticorebot.protocol.task_models import ContentBlock


@dataclass(slots=True)
class NormalizedInput:
    session_id: str
    turn_id: str
    channel_kind: str = "chat"
    input_kind: str = "text"
    plain_text: str | None = None
    content_blocks: list[ContentBlock] = field(default_factory=list)
    attachments: list[ContentBlock] = field(default_factory=list)
    barge_in: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["NormalizedInput"]
