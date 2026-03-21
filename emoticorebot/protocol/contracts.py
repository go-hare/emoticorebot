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
ExecutorJobAction = Literal["execute", "cancel"]
ExecutorDecision = Literal["accept", "reject"]
TaskMode = Literal["skip", "sync", "async"]


__all__ = [
    "ChannelKind",
    "DeliveryMode",
    "InputKind",
    "InputMode",
    "ReplyDeliveryMode",
    "ExecutorDecision",
    "ExecutorJobAction",
    "SessionMode",
    "StreamState",
    "TaskMode",
]
