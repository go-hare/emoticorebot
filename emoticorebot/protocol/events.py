"""Event payload models for the runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import (
    ChannelKind,
    DeliveryMode,
    ExecutionDecision,
    ExecutionTaskAction,
    InputKind,
    ReplyDeliveryMode,
    SessionMode,
    StreamState,
)
from .task_models import ContentBlock, MessageRef, PerceptionData, ProtocolModel, ReplyDraft, TraceItem

PerceptionType = Literal["wake_word", "vision", "proximity", "localization"]
SignalType = Literal["timeout", "backpressure", "health_warning", "warning"]
TaskEvent = dict[str, Any]

FollowupSourceEvent = Literal[
    "execution.event.task_accepted",
    "execution.event.progress",
    "execution.event.result_ready",
    "execution.event.task_rejected",
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


class MainBrainReplyReadyPayload(ProtocolModel):
    request_id: str | None = None
    reply_text: str
    reply_kind: Literal["answer", "status"] = "answer"
    delivery_target: DeliveryTargetPayload
    origin_message: MessageRef | None = None
    invoke_execution: bool = False
    execution_request: dict[str, Any] = Field(default_factory=dict)
    related_task_id: str | None = None
    stream_id: str | None = None
    stream_state: StreamState | None = None
    stream_index: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stream_fields(self) -> "MainBrainReplyReadyPayload":
        if self.stream_state is not None and not self.stream_id:
            raise ValueError("main brain replies with stream_state require stream_id")
        return self


class MainBrainStreamDeltaPayload(ProtocolModel):
    stream_id: str
    delta_text: str
    stream_state: StreamState = "delta"
    stream_index: int | None = None
    origin_message: MessageRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MainBrainFollowupReadyPayload(ProtocolModel):
    job_id: str
    source_event: FollowupSourceEvent
    source_decision: ExecutionDecision
    reply_text: str
    reply_kind: Literal["answer", "status"] = "status"
    delivery_target: DeliveryTargetPayload
    origin_message: MessageRef | None = None
    related_task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionAcceptedPayload(ProtocolModel):
    job_id: str
    decision: Literal["accept"] = "accept"
    stage: str | None = None
    reason: str | None = None
    estimated_duration_s: int | None = None
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionProgressPayload(ProtocolModel):
    job_id: str
    decision: Literal["accept"] = "accept"
    stage: str | None = None
    summary: str
    progress: float | None = None
    next_step: str | None = None
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionRejectedPayload(ProtocolModel):
    job_id: str
    decision: Literal["reject"] = "reject"
    reason: str
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResultPayload(ProtocolModel):
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
    "ExecutionAcceptedPayload",
    "ExecutionDecision",
    "ExecutionProgressPayload",
    "ExecutionRejectedPayload",
    "ExecutionResultPayload",
    "ExecutionTaskAction",
    "FollowupSourceEvent",
    "InputSlots",
    "MainBrainFollowupReadyPayload",
    "MainBrainReplyReadyPayload",
    "MainBrainStreamDeltaPayload",
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
    "TraceItem",
    "TurnInputPayload",
]
