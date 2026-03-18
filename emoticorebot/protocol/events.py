"""Event payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import (
    ChannelKind,
    DeliveryMode,
    InputKind,
    InputMode,
    ReplyDeliveryMode,
    RightBrainJobAction,
    RightBrainStrategy,
    SessionMode,
    TaskCommandType,
    TaskEventType,
)
from .task_models import (
    AgentRole,
    ContentBlock,
    InputRequest,
    MessageRef,
    PerceptionData,
    PlanStep,
    ProtocolModel,
    ReplyDraft,
    ReviewItem,
    TaskRequestSpec,
    TaskStateSnapshot,
    TaskVisibleResult,
    TaskVisibleState,
    TraceItem,
)

PerceptionType = Literal["wake_word", "vision", "proximity", "localization"]
SignalType = Literal["timeout", "backpressure", "health_warning", "warning"]
TaskEvent = dict[str, Any]


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


class IntentScoredPayload(ProtocolModel):
    input_mode: InputMode = "turn"
    session_mode: SessionMode = "turn_chat"
    source_text: str
    scores: dict[str, float] = Field(default_factory=dict)
    intent_tags: list[str] = Field(default_factory=list)
    emotion_tags: list[str] = Field(default_factory=list)
    route_hint: str | None = None
    input_slots: InputSlots = Field(default_factory=InputSlots)
    right_brain_strategy: RightBrainStrategy = "skip"
    invoke_right_brain: bool = False
    reason: str | None = None


class LeftReplyReadyPayload(ProtocolModel):
    request_id: str | None = None
    reply_text: str
    reply_kind: Literal["answer", "ask_user", "status"] = "answer"
    right_brain_strategy: RightBrainStrategy = "skip"
    invoke_right_brain: bool = False
    right_brain_request: dict[str, Any] = Field(default_factory=dict)
    related_task_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainAcceptedPayload(ProtocolModel):
    job_id: str
    decision: Literal["accept"] = "accept"
    stage: str | None = None
    reason: str | None = None
    estimated_duration_s: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainClarifyPayload(ProtocolModel):
    job_id: str
    decision: Literal["clarify"] = "clarify"
    question: str
    missing_fields: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainRejectedPayload(ProtocolModel):
    job_id: str
    decision: Literal["reject"] = "reject"
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RightBrainResultPayload(ProtocolModel):
    job_id: str
    job_action: RightBrainJobAction
    task_id: str | None = None
    summary: str | None = None
    result_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStartedReportPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    assignment_id: str
    started_at: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskProgressReportPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    assignment_id: str
    summary: str | None = None
    detail: str | None = None
    progress: float | None = None
    current_step_id: str | None = None
    next_step: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskNeedInputReportPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    assignment_id: str
    input_request: InputRequest
    summary: str | None = None
    partial_result: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskPlanReadyReportPayload(ProtocolModel):
    task_id: str
    assignment_id: str
    plan_id: str
    summary: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    reviewer_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResultReportPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    assignment_id: str
    summary: str | None = None
    result_text: str | None = None
    result_blocks: list[ContentBlock] = Field(default_factory=list)
    artifacts: list[ContentBlock] = Field(default_factory=list)
    confidence: float | None = None
    reviewer_required: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskApprovedReportPayload(ProtocolModel):
    task_id: str
    review_id: str
    summary: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskRejectedReportPayload(ProtocolModel):
    task_id: str
    review_id: str
    summary: str | None = None
    rejection_reason: str | None = None
    findings: list[ReviewItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskFailedReportPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    assignment_id: str
    reason: str | None = None
    summary: str | None = None
    retryable: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCancelledReportPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    assignment_id: str
    reason: str | None = None
    cancelled_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskNotificationPayload(ProtocolModel):
    task_id: str
    state: TaskVisibleState
    result: TaskVisibleResult = "none"
    updated_at: str | None = None
    trace_append: list[TraceItem] = Field(default_factory=list)


class TaskUpdatePayload(TaskNotificationPayload):
    state: Literal["running"] = "running"
    message: str
    progress: float | None = None
    stage: str | None = None


class TaskSummaryPayload(TaskNotificationPayload):
    state: Literal["running"] = "running"
    summary: str
    stage: str | None = None
    next_step: str | None = None


class TaskAskPayload(TaskNotificationPayload):
    state: Literal["waiting"] = "waiting"
    question: str
    field: str | None = None
    why: str | None = None


class TaskEndPayload(TaskNotificationPayload):
    state: Literal["done"] = "done"
    result: Literal["success", "failed", "cancelled"]
    summary: str | None = None
    output: str | None = None
    error: str | None = None
    trace_final: list[TraceItem] = Field(default_factory=list)


class ReplyReadyPayload(ProtocolModel):
    reply: ReplyDraft
    origin_message: MessageRef | None = None
    related_task_id: str | None = None
    related_event_id: str | None = None
    channel_override: str | None = None
    chat_id_override: str | None = None
    delivery_mode: DeliveryMode = "inline"
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    retryable: bool | None = None
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
    "InputSlots",
    "IntentScoredPayload",
    "LeftReplyReadyPayload",
    "PerceptionEventPayload",
    "PerceptionType",
    "RightBrainAcceptedPayload",
    "RightBrainClarifyPayload",
    "RightBrainJobAction",
    "RightBrainRejectedPayload",
    "RightBrainResultPayload",
    "RightBrainStrategy",
    "ReplyBlockedPayload",
    "ReplyReadyPayload",
    "RepliedPayload",
    "SessionMode",
    "SignalType",
    "StreamChunkPayload",
    "StreamCommitPayload",
    "StreamInterruptedPayload",
    "StreamStartPayload",
    "SystemSignalPayload",
    "TaskApprovedReportPayload",
    "TaskAskPayload",
    "TaskCancelledReportPayload",
    "TaskCommandType",
    "TaskEndPayload",
    "TaskEvent",
    "TaskEventType",
    "TaskFailedReportPayload",
    "TaskNeedInputReportPayload",
    "TaskNotificationPayload",
    "TaskPlanReadyReportPayload",
    "TaskProgressReportPayload",
    "TaskRejectedReportPayload",
    "TaskResultReportPayload",
    "TaskStartedReportPayload",
    "TaskSummaryPayload",
    "TaskUpdatePayload",
    "TurnInputPayload",
]
