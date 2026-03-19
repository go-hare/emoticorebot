"""Command payload models for the active runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import RightBrainDecision, RightBrainJobAction
from .events import DeliveryTargetPayload, TurnInputPayload
from .task_models import ControlParameters, ProtocolModel

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
    delivery_target: DeliveryTargetPayload
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
    job_action: RightBrainJobAction
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
    "FollowupContextPayload",
    "LeftReplyRequestPayload",
    "RightBrainJobAction",
    "RightBrainJobRequestPayload",
]
