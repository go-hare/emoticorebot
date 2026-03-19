"""Event payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import (
    ChannelKind,
    DeliveryMode,
    InputKind,
    ReplyDeliveryMode,
    RightBrainDecision,
    RightBrainJobAction,
    SessionMode,
    StreamState,
)
from .task_models import (
    ContentBlock,
    MessageRef,
    PerceptionData,
    ProtocolModel,
    ReplyDraft,
    TraceItem,
)

PerceptionType = Literal["wake_word", "vision", "proximity", "localization"]
SignalType = Literal["timeout", "backpressure", "health_warning", "warning"]
TaskEvent = dict[str, Any]

FollowupSourceEvent = Literal[
    "right.event.job_accepted",
    "right.event.progress",
    "right.event.result_ready",
    "right.event.job_rejected",
]


class InputSlots(ProtocolModel):
    user: str = ""
    task: str = ""


class TurnInputPayload(ProtocolModel):
    input_mode: Literal["turn"] = "turn"
    session_mode: SessionMode = "turn_chat"
    channel_kind: ChannelKind = "chat"
    input_kind: InputKind = "text"
    message: MessageRef
    user_text: str | None = None
    input_slots: InputSlots = Field(default_factory=InputSlots)
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    attachments: list[ContentBlock] = Field(default_factory=list)
    barge_in: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    input_id: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "TurnInputPayload":
        if not self.user_text and not self.content_blocks:
            raise ValueError("turn inputs require user_text or content_blocks")
        if not self.message.channel or not self.message.chat_id or not self.message.message_id:
            raise ValueError("turn inputs require channel, chat_id, and message_id")
        return self


class StreamStartPayload(ProtocolModel):
    input_mode: Literal["stream"] = "stream"
    session_mode: SessionMode = "realtime_chat"
    stream_id: str
    message: MessageRef
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamChunkPayload(ProtocolModel):
    input_mode: Literal["stream"] = "stream"
    stream_id: str
    chunk_index: int
    chunk_text: str
    is_commit_point: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamCommitPayload(ProtocolModel):
    input_mode: Literal["stream"] = "stream"
    stream_id: str
    committed_text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamInterruptedPayload(ProtocolModel):
    input_mode: Literal["stream"] = "stream"
    stream_id: str
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryTargetPayload(ProtocolModel):
    delivery_mode: DeliveryMode
    channel: str | None = None
    chat_id: str | None = None


class MemoryCandidatePayload(ProtocolModel):
    kind: str
    summary: str


class LeftReplyReadyPayload(ProtocolModel):
    request_id: str | None = None
    reply_text: str
    reply_kind: Literal["answer", "status"] = "answer"
    delivery_target: DeliveryTargetPayload
    origin_message: MessageRef | None = None
    invoke_right_brain: bool = False
    right_brain_request: dict[str, Any] = Field(default_factory=dict)
    related_task_id: str | None = None
    stream_id: str | None = None
    stream_state: StreamState | None = None
    stream_index: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stream_fields(self) -> "LeftReplyReadyPayload":
        if self.stream_state is not None and not self.stream_id:
            raise ValueError("left replies with stream_state require stream_id")
        return self


class LeftStreamDeltaPayload(ProtocolModel):
    stream_id: str
    delta_text: str
    stream_state: StreamState = "delta"
    stream_index: int | None = None
    origin_message: MessageRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeftFollowupReadyPayload(ProtocolModel):
    job_id: str
    source_event: FollowupSourceEvent
    source_decision: RightBrainDecision
    reply_text: str
    reply_kind: Literal["answer", "status"] = "status"
    delivery_target: DeliveryTargetPayload
    origin_message: MessageRef | None = None
    related_task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainAcceptedPayload(ProtocolModel):
    job_id: str
    decision: Literal["accept"] = "accept"
    stage: str | None = None
    reason: str | None = None
    estimated_duration_s: int | None = None
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainProgressPayload(ProtocolModel):
    job_id: str
    decision: Literal["accept"] = "accept"
    stage: str | None = None
    summary: str
    progress: float | None = None
    next_step: str | None = None
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainRejectedPayload(ProtocolModel):
    job_id: str
    decision: Literal["reject"] = "reject"
    reason: str
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainResultPayload(ProtocolModel):
    job_id: str
    decision: Literal["accept", "answer_only"] = "accept"
    summary: str | None = None
    result_text: str | None = None
    artifacts: list[ContentBlock] = Field(default_factory=list)
    delivery_target: DeliveryTargetPayload
    memory_candidate: MemoryCandidatePayload | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutputReadyPayloadBase(ProtocolModel):
    output_id: str
    delivery_target: DeliveryTargetPayload
    content: ReplyDraft
    origin_message: MessageRef | None = None
    related_task_id: str | None = None
    related_event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content(self) -> "OutputReadyPayloadBase":
        if not str(self.output_id or "").strip():
            raise ValueError("output events require output_id")
        return self


class OutputInlineReadyPayload(OutputReadyPayloadBase):
    @model_validator(mode="after")
    def validate_delivery_mode(self) -> "OutputInlineReadyPayload":
        if self.delivery_target.delivery_mode != "inline":
            raise ValueError("inline output events require delivery_mode=inline")
        return self


class OutputPushReadyPayload(OutputReadyPayloadBase):
    @model_validator(mode="after")
    def validate_delivery_mode(self) -> "OutputPushReadyPayload":
        if self.delivery_target.delivery_mode != "push":
            raise ValueError("push output events require delivery_mode=push")
        return self


class OutputStreamPayloadBase(OutputReadyPayloadBase):
    stream_id: str
    stream_state: StreamState
    stream_index: int | None = None

    @model_validator(mode="after")
    def validate_stream_fields(self) -> "OutputStreamPayloadBase":
        if self.delivery_target.delivery_mode != "stream":
            raise ValueError("stream output events require delivery_mode=stream")
        if not str(self.stream_id or "").strip():
            raise ValueError("stream output events require stream_id")
        return self


class OutputStreamOpenPayload(OutputStreamPayloadBase):
    stream_state: Literal["open"] = "open"


class OutputStreamDeltaPayload(OutputStreamPayloadBase):
    stream_state: Literal["delta"] = "delta"


class OutputStreamClosePayload(OutputStreamPayloadBase):
    stream_state: Literal["close", "superseded"] = "close"


class ReplyBlockedPayload(ProtocolModel):
    reply: ReplyDraft
    block_reason: str
    policy_name: str
    redaction_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepliedPayload(ProtocolModel):
    reply_id: str
    delivery_message: MessageRef
    delivery_mode: ReplyDeliveryMode
    delivered_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryFailedPayload(ProtocolModel):
    reply_id: str
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PerceptionEventPayload(ProtocolModel):
    sensor_id: str
    perception_type: PerceptionType
    summary: str | None = None
    data: PerceptionData | None = None
    confidence: float | None = None
    observed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemSignalPayload(ProtocolModel):
    signal_id: str
    signal_type: SignalType
    reason: str | None = None
    related_event_id: str | None = None
    related_task_id: str | None = None
    severity: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "DeliveryFailedPayload",
    "DeliveryTargetPayload",
    "FollowupSourceEvent",
    "InputSlots",
    "LeftFollowupReadyPayload",
    "LeftReplyReadyPayload",
    "LeftStreamDeltaPayload",
    "MemoryCandidatePayload",
    "OutputInlineReadyPayload",
    "OutputPushReadyPayload",
    "OutputReadyPayloadBase",
    "OutputStreamClosePayload",
    "OutputStreamDeltaPayload",
    "OutputStreamOpenPayload",
    "OutputStreamPayloadBase",
    "PerceptionEventPayload",
    "PerceptionType",
    "RightBrainAcceptedPayload",
    "RightBrainProgressPayload",
    "RightBrainJobAction",
    "RightBrainRejectedPayload",
    "RightBrainResultPayload",
    "ReplyBlockedPayload",
    "RepliedPayload",
    "SessionMode",
    "SignalType",
    "StreamChunkPayload",
    "StreamCommitPayload",
    "StreamInterruptedPayload",
    "StreamStartPayload",
    "SystemSignalPayload",
    "TaskEvent",
    "TurnInputPayload",
]
