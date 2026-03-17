"""Event payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

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
TaskCommandType = Literal["create", "resume", "cancel"]
TaskEventType = Literal["update", "summary", "ask", "end"]
TaskEvent = dict[str, Any]


class StableInputPayload(ProtocolModel):
    message: MessageRef
    plain_text: str | None = None
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    attachments: list[ContentBlock] = Field(default_factory=list)
    is_interrupt: bool | None = None
    is_follow_up: bool | None = None
    detected_language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    input_id: str | None = None
    input_kind: Literal["text", "voice", "video", "multimodal"] = "text"
    channel_kind: Literal["chat", "voice", "video"] = "chat"
    barge_in: bool | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "StableInputPayload":
        if not self.plain_text and not self.content_blocks:
            raise ValueError("stable inputs require plain_text or content_blocks")
        if not self.message.channel or not self.message.chat_id or not self.message.message_id:
            raise ValueError("stable inputs require channel, chat_id, and message_id")
        return self


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
    delivery_mode: str | None = None
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
    delivery_mode: str | None = None
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
    "PerceptionEventPayload",
    "PerceptionType",
    "ReplyBlockedPayload",
    "ReplyReadyPayload",
    "RepliedPayload",
    "SignalType",
    "StableInputPayload",
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
]
