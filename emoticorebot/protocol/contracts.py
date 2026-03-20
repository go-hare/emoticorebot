"""Shared protocol enums and literals for the runtime."""

from __future__ import annotations

from typing import Literal

InputMode = Literal["turn", "stream"]
SessionMode = Literal["turn_chat", "realtime_chat"]
ChannelKind = Literal["chat", "voice", "video"]
InputKind = Literal["text", "voice", "multimodal"]
DeliveryMode = Literal["inline", "push", "stream"]
ReplyDeliveryMode = Literal["inline", "push", "stream", "suppressed"]
StreamState = Literal["open", "delta", "close", "superseded"]
ExecutionTaskAction = Literal["create_task", "cancel_task"]
ExecutionDecision = Literal["accept", "answer_only", "reject"]
TaskMode = Literal["skip", "sync", "async"]


__all__ = [
    "ChannelKind",
    "DeliveryMode",
    "ExecutionDecision",
    "ExecutionTaskAction",
    "InputKind",
    "InputMode",
    "ReplyDeliveryMode",
    "SessionMode",
    "StreamState",
    "TaskMode",
]
