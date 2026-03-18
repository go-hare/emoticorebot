"""Shared protocol enums/literals for the v3 runtime."""

from __future__ import annotations

from typing import Literal

InputMode = Literal["turn", "stream"]
SessionMode = Literal["turn_chat", "realtime_chat"]
ChannelKind = Literal["chat", "voice", "video"]
InputKind = Literal["text", "voice", "multimodal"]
DeliveryMode = Literal["inline", "push", "stream"]
ReplyDeliveryMode = Literal["inline", "push", "stream", "suppressed"]
StreamState = Literal["open", "delta", "close", "superseded"]
TaskCommandType = Literal["create", "resume", "cancel"]
TaskEventType = Literal["update", "summary", "ask", "end"]
RightBrainStrategy = Literal["skip", "sync", "async"]
RightBrainJobAction = Literal["create_task", "resume_task", "cancel_task"]


__all__ = [
    "ChannelKind",
    "DeliveryMode",
    "InputKind",
    "InputMode",
    "ReplyDeliveryMode",
    "RightBrainJobAction",
    "RightBrainStrategy",
    "SessionMode",
    "StreamState",
    "TaskCommandType",
    "TaskEventType",
]
