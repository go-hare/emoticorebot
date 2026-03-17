"""Command payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .task_models import (
    AgentInputContext,
    AgentRole,
    ControlParameters,
    MessageRef,
    PlanStep,
    ProtocolModel,
    ProvidedInputBundle,
    ReplyDraft,
    ReviewerContext,
    TaskRequestSpec,
    TaskStateSnapshot,
)

ControlAction = Literal["speak", "move", "stop", "manipulate"]


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
    message: str | None = None


class TaskCancelPayload(ProtocolModel):
    command_id: str
    task_id: str
    reason: str | None = None
    by: Literal["user", "brain", "system"] | None = None


class BrainReplyPayload(ProtocolModel):
    command_id: str
    reply: ReplyDraft
    related_task_id: str | None = None
    origin_message: MessageRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    "BrainReplyPayload",
    "CancelAgentPayload",
    "ControlAction",
    "ControlCommandPayload",
    "ResumeAgentPayload",
    "TaskCancelPayload",
    "TaskCreatePayload",
    "TaskResumePayload",
]
