"""Command payload models for the active runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import ExecutionDecision, ExecutionTaskAction
from .events import DeliveryTargetPayload, TurnInputPayload
from .task_models import ControlParameters, ProtocolModel

ControlAction = Literal["speak", "move", "stop", "manipulate"]


FollowupSourceEvent = Literal[
    "execution.event.task_accepted",
    "execution.event.progress",
    "execution.event.result_ready",
    "execution.event.task_rejected",
]


class FollowupContextPayload(ProtocolModel):
    source_event: FollowupSourceEvent
    job_id: str
    decision: ExecutionDecision
    stage: str | None = None
    summary: str | None = None
    progress: float | None = None
    next_step: str | None = None
    result_text: str | None = None
    reason: str | None = None
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class MainBrainReplyRequestPayload(ProtocolModel):
    request_id: str
    turn_input: TurnInputPayload | None = None
    followup_context: FollowupContextPayload | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "MainBrainReplyRequestPayload":
        if (self.turn_input is None) == (self.followup_context is None):
            raise ValueError("main brain reply requests require exactly one of turn_input or followup_context")
        return self


class ExecutionTaskRequestPayload(ProtocolModel):
    job_id: str
    job_action: ExecutionTaskAction
    job_kind: str | None = None
    source_text: str | None = None
    request_text: str | None = None
    task_id: str | None = None
    goal: str | None = None
    delivery_target: DeliveryTargetPayload
    scores: dict[str, float] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlCommandPayload(ProtocolModel):
    command_id: str
    action: ControlAction
    target: str | None = None
    parameters: ControlParameters | None = None
    safety_level: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ControlAction",
    "ControlCommandPayload",
    "ExecutionTaskAction",
    "ExecutionTaskRequestPayload",
    "FollowupContextPayload",
    "MainBrainReplyRequestPayload",
]
