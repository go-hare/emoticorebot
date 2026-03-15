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
)

PerceptionType = Literal["wake_word", "vision", "proximity", "localization"]
SignalType = Literal["timeout", "backpressure", "health_warning", "warning"]
TaskEventType = Literal[
    "created",
    "assigned",
    "started",
    "progress",
    "need_input",
    "planned",
    "reviewing",
    "approved",
    "rejected",
    "result",
    "failed",
    "cancelled",
]
TaskEvent = dict[str, Any]


class UserMessagePayload(ProtocolModel):
    message: MessageRef
    plain_text: str | None = None
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    attachments: list[ContentBlock] = Field(default_factory=list)
    is_interrupt: bool | None = None
    is_follow_up: bool | None = None
    detected_language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content(self) -> "UserMessagePayload":
        if not self.plain_text and not self.content_blocks:
            raise ValueError("user messages require plain_text or content_blocks")
        if not self.message.channel or not self.message.chat_id or not self.message.message_id:
            raise ValueError("user messages require channel, chat_id, and message_id")
        return self


class InterruptPayload(ProtocolModel):
    message: MessageRef
    interrupt_type: str
    plain_text: str | None = None
    target_task_id: str | None = None
    urgent: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceChunkPayload(ProtocolModel):
    message: MessageRef
    stream_id: str
    chunk_index: int
    audio: ContentBlock
    is_final_chunk: bool | None = None
    vad_state: str | None = None
    partial_transcript: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelAttachmentPayload(ProtocolModel):
    message: MessageRef
    attachments: list[ContentBlock] = Field(default_factory=list)
    attachment_count: int | None = None
    extracted_text: str | None = None
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


class TaskEventBasePayload(ProtocolModel):
    task_id: str
    state: TaskStateSnapshot
    summary: str | None = None
    assignee: str | None = None
    input_request: InputRequest | None = None
    plan_id: str | None = None
    review_required: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCreatedEventPayload(TaskEventBasePayload):
    task_request: TaskRequestSpec
    origin_message: MessageRef


class TaskAssignedEventPayload(TaskEventBasePayload):
    assignment_id: str
    agent_role: AgentRole


class TaskStartedEventPayload(TaskEventBasePayload):
    assignment_id: str
    agent_role: AgentRole
    started_at: str | None = None


class TaskProgressEventPayload(TaskEventBasePayload):
    progress: float | None = None
    detail: str | None = None
    current_step_id: str | None = None
    next_step: str | None = None


class TaskNeedInputEventPayload(TaskEventBasePayload):
    input_request: InputRequest
    partial_result: str | None = None


class TaskPlannedEventPayload(TaskEventBasePayload):
    plan_id: str
    steps: list[PlanStep] = Field(default_factory=list)


class TaskReviewingEventPayload(TaskEventBasePayload):
    review_id: str
    reviewer_role: AgentRole


class TaskApprovedEventPayload(TaskEventBasePayload):
    review_id: str
    notes: str | None = None


class TaskRejectedEventPayload(TaskEventBasePayload):
    review_id: str
    rejection_reason: str | None = None
    findings: list[ReviewItem] = Field(default_factory=list)


class TaskResultEventPayload(TaskEventBasePayload):
    result_text: str | None = None
    result_blocks: list[ContentBlock] = Field(default_factory=list)
    artifacts: list[ContentBlock] = Field(default_factory=list)
    confidence: float | None = None


class TaskFailedEventPayload(TaskEventBasePayload):
    reason: str | None = None
    retryable: bool | None = None


class TaskCancelledEventPayload(TaskEventBasePayload):
    reason: str | None = None
    cancelled_by: str | None = None


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
    "ChannelAttachmentPayload",
    "DeliveryFailedPayload",
    "InterruptPayload",
    "PerceptionEventPayload",
    "PerceptionType",
    "ReplyBlockedPayload",
    "ReplyReadyPayload",
    "RepliedPayload",
    "SignalType",
    "SystemSignalPayload",
    "TaskEvent",
    "TaskEventType",
    "TaskApprovedEventPayload",
    "TaskApprovedReportPayload",
    "TaskAssignedEventPayload",
    "TaskCancelledEventPayload",
    "TaskCancelledReportPayload",
    "TaskCreatedEventPayload",
    "TaskEventBasePayload",
    "TaskFailedEventPayload",
    "TaskFailedReportPayload",
    "TaskNeedInputEventPayload",
    "TaskNeedInputReportPayload",
    "TaskPlanReadyReportPayload",
    "TaskPlannedEventPayload",
    "TaskProgressEventPayload",
    "TaskProgressReportPayload",
    "TaskRejectedEventPayload",
    "TaskRejectedReportPayload",
    "TaskResultEventPayload",
    "TaskResultReportPayload",
    "TaskReviewingEventPayload",
    "TaskStartedEventPayload",
    "TaskStartedReportPayload",
    "UserMessagePayload",
    "VoiceChunkPayload",
]
