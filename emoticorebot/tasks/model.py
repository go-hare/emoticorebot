"""Task-system data models shared by runtime and central."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from emoticorebot.tasks.state_machine import (
    TaskControlState,
    TaskMode,
    TaskRecommendedAction,
    TaskStatus,
)


class CentralResultPacket(TypedDict, total=False):
    """Normalized central result packet returned into the turn loop."""

    control_state: TaskControlState
    status: TaskStatus
    analysis: str
    risks: list[str]
    missing: list[str]
    recommended_action: TaskRecommendedAction
    confidence: float
    pending_review: dict[str, Any]
    thread_id: str
    run_id: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class TaskState:
    """Runtime state for the current task handled by central."""

    task_id: str = ""
    title: str = ""
    goal: str = ""
    brief: str = ""
    owner_agent: str = "central"
    created_by: str = "brain"
    mode: TaskMode = "sync"
    priority: str = "normal"
    plan: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    thread_id: str = ""
    run_id: str = ""
    control_state: TaskControlState = "idle"
    status: TaskStatus = "none"
    analysis: str = ""
    result_summary: str = ""
    risks: list[str] = field(default_factory=list)
    recommended_action: TaskRecommendedAction = ""
    confidence: float = 0.0
    missing: list[str] = field(default_factory=list)
    pending_review: dict[str, Any] = field(default_factory=dict)
    need_user_input: bool = False
    attempts: int = 0
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def request(self) -> str:
        return self.brief

    @request.setter
    def request(self, value: str) -> None:
        self.brief = str(value or "")

    @property
    def final_result(self) -> str:
        return self.result_summary

    @final_result.setter
    def final_result(self, value: str) -> None:
        self.result_summary = str(value or "")


__all__ = ["CentralResultPacket", "TaskState"]
