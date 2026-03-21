"""Command payload models for the active runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import ExecutorDecision, ExecutorJobAction
from .events import DeliveryTargetPayload, TurnInputPayload
from .task_models import ControlParameters, ProtocolModel

ControlAction = Literal["speak", "move", "stop", "manipulate"]


ExecutorResultSourceEvent = Literal[
    "executor.event.result_ready",
    "executor.event.job_rejected",
]


class ExecutorResultContextPayload(ProtocolModel):
    source_event: ExecutorResultSourceEvent
    job_id: str
    decision: ExecutorDecision
    summary: str | None = None
    result_text: str | None = None
    reason: str | None = None
    delivery_target: DeliveryTargetPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrainReplyRequestPayload(ProtocolModel):
    request_id: str
    turn_input: TurnInputPayload | None = None
    executor_result: ExecutorResultContextPayload | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "BrainReplyRequestPayload":
        if (self.turn_input is None) == (self.executor_result is None):
            raise ValueError("brain reply requests require exactly one of turn_input or executor_result")
        return self


class ExecutorJobRequestPayload(ProtocolModel):
    job_id: str
    job_action: ExecutorJobAction
    job_kind: str | None = None
    source_text: str | None = None
    request_text: str | None = None
    task_id: str | None = None
    goal: str | None = None
    mainline: list[Any] = Field(default_factory=list)
    current_stage: str | list[str] | None = None
    current_checks: list[str] = Field(default_factory=list)
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
    "BrainReplyRequestPayload",
    "ExecutorResultContextPayload",
    "ExecutorJobAction",
    "ExecutorJobRequestPayload",
]
