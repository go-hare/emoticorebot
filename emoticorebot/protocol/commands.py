"""Command payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import DeliveryMode, RightBrainDecision, RightBrainJobAction, RightBrainStrategy
from .events import DeliveryTargetPayload, TurnInputPayload
from .task_models import (
    AgentInputContext,
    AgentRole,
    ControlParameters,
    MessageRef,
    PlanStep,
    ProtocolModel,
    ProvidedInputBundle,
    ReviewerContext,
    TaskRequestSpec,
    TaskStateSnapshot,
)

ControlAction = Literal["speak", "move", "stop", "manipulate"]


FollowupSourceEvent = Literal[
    "right.event.job_accepted",
    "right.event.progress",
    "right.event.result_ready",
    "right.event.job_rejected",
]


class FollowupContextPayload(ProtocolModel):
    source_event: FollowupSourceEvent
    job_id: str
    decision: RightBrainDecision
    stage: str | None = None
    summary: str | None = None
    progress: float | None = None
    next_step: str | None = None
    result_text: str | None = None
    reason: str | None = None
    preferred_delivery_mode: DeliveryMode = "push"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeftReplyRequestPayload(ProtocolModel):
    request_id: str
    turn_input: TurnInputPayload | None = None
    followup_context: FollowupContextPayload | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "LeftReplyRequestPayload":
        if (self.turn_input is None) == (self.followup_context is None):
            raise ValueError("left reply requests require exactly one of turn_input or followup_context")
        return self


class RightBrainJobRequestPayload(ProtocolModel):
    job_id: str
    right_brain_strategy: RightBrainStrategy = "async"
    job_action: RightBrainJobAction
    job_kind: str | None = None
    source_text: str | None = None
    request_text: str | None = None
    task_id: str | None = None
    goal: str | None = None
    delivery_target: DeliveryTargetPayload | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCreatePayload(ProtocolModel):
    command_id: str
    request: str
    goal: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


class TaskResumePayload(ProtocolModel):
    command_id: str
    task_id: str
    state: Literal["running"] = "running"
    user_input: str | None = None
    provided_inputs: ProvidedInputBundle | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


class TaskCancelPayload(ProtocolModel):
    command_id: str
    task_id: str
    reason: str | None = None
    by: Literal["user", "brain", "system"] | None = None


class AssignAgentPayload(ProtocolModel):
    assignment_id: str
    task_id: str
    agent_role: AgentRole
    task_state: TaskStateSnapshot
    task_request: TaskRequestSpec | None = None
    plan_steps: list[PlanStep] = Field(default_factory=list)
    input_context: AgentInputContext | None = None
    reviewer_context: ReviewerContext | None = None
    deadline_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResumeAgentPayload(ProtocolModel):
    assignment_id: str
    task_id: str
    agent_role: AgentRole
    task_state: TaskStateSnapshot
    resume_input: ProvidedInputBundle
    resume_message: MessageRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelAgentPayload(ProtocolModel):
    task_id: str
    agent_role: AgentRole
    reason: str | None = None
    hard_stop: bool | None = None
    deadline_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArchiveTaskPayload(ProtocolModel):
    task_id: str
    archive_reason: str | None = None
    final_state: TaskStateSnapshot
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlCommandPayload(ProtocolModel):
    command_id: str
    action: ControlAction
    target: str | None = None
    parameters: ControlParameters | None = None
    safety_level: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ArchiveTaskPayload",
    "AssignAgentPayload",
    "CancelAgentPayload",
    "ControlAction",
    "ControlCommandPayload",
    "FollowupContextPayload",
    "LeftReplyRequestPayload",
    "RightBrainJobAction",
    "RightBrainJobRequestPayload",
    "RightBrainStrategy",
    "ResumeAgentPayload",
    "TaskCancelPayload",
    "TaskCreatePayload",
    "TaskResumePayload",
]
