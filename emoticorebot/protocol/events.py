"""Typed runtime events and the transitional task event schema."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from .task_models import (
    ReviewItem,
    TaskControlState,
    TaskInputRequest,
    TaskResultStatus,
    TaskSpec,
    TaskState,
    TraceItem,
)
from .task_result import TaskExecutionResult

TaskEventType = Literal["created", "started", "progress", "need_input", "done", "failed", "cancelled"]


class TaskEvent(TypedDict, total=False):
    """当前 task runtime 发回主流程的过渡事件包。"""

    task_id: str
    channel: str
    chat_id: str
    message_id: str
    type: TaskEventType
    title: str
    params: TaskSpec
    message: str
    summary: str
    reason: str
    field: str
    question: str
    control_state: TaskControlState
    result_status: TaskResultStatus
    analysis: str
    missing: list[str]
    pending_review: list[ReviewItem]
    recommended_action: str
    confidence: float
    attempt_count: int
    task_trace: list[TraceItem]
    payload: dict[str, Any]


RuntimeEventType = Literal[
    "turn_started",
    "turn_completed",
    "task_created",
    "task_started",
    "task_progress",
    "task_awaiting_user",
    "task_completed",
    "task_failed",
    "task_cancelled",
    "task_state_updated",
    "runtime_warning",
]


class RuntimeEventBase(TypedDict, total=False):
    """未来 SessionRuntime 对外发出的统一事件基类。"""

    event_type: RuntimeEventType
    session_id: str
    thread_id: str
    turn_id: str
    task_id: str
    emitted_at: str
    payload: dict[str, Any]


class TurnStartedEvent(RuntimeEventBase, total=False):
    event_type: Literal["turn_started"]
    channel: str
    chat_id: str
    message_id: str
    user_input: str


class TurnCompletedEvent(RuntimeEventBase, total=False):
    event_type: Literal["turn_completed"]
    message_id: str
    assistant_output: str


class TaskCreatedEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_created"]
    title: str
    task_spec: TaskSpec


class TaskStartedEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_started"]
    title: str
    task_spec: TaskSpec


class TaskProgressEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_progress"]
    title: str
    message: str
    stage_info: str
    state: TaskState


class TaskAwaitingUserEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_awaiting_user"]
    title: str
    input_request: TaskInputRequest
    state: TaskState


class TaskCompletedEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_completed"]
    title: str
    result: TaskExecutionResult
    state: TaskState


class TaskFailedEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_failed"]
    title: str
    reason: str
    result: TaskExecutionResult
    state: TaskState


class TaskCancelledEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_cancelled"]
    title: str
    reason: str
    state: TaskState


class TaskStateUpdatedEvent(RuntimeEventBase, total=False):
    event_type: Literal["task_state_updated"]
    state: TaskState


class RuntimeWarningEvent(RuntimeEventBase, total=False):
    event_type: Literal["runtime_warning"]
    message: str
    warning_code: str


RuntimeEvent = (
    TurnStartedEvent
    | TurnCompletedEvent
    | TaskCreatedEvent
    | TaskStartedEvent
    | TaskProgressEvent
    | TaskAwaitingUserEvent
    | TaskCompletedEvent
    | TaskFailedEvent
    | TaskCancelledEvent
    | TaskStateUpdatedEvent
    | RuntimeWarningEvent
)


__all__ = [
    "RuntimeEvent",
    "RuntimeEventType",
    "TaskAwaitingUserEvent",
    "TaskCancelledEvent",
    "TaskCompletedEvent",
    "TaskCreatedEvent",
    "TaskEvent",
    "TaskEventType",
    "TaskFailedEvent",
    "TaskProgressEvent",
    "TaskStartedEvent",
    "TaskStateUpdatedEvent",
    "TurnCompletedEvent",
    "TurnStartedEvent",
]
