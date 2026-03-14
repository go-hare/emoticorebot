"""Typed submissions accepted by RuntimeManager / SessionRuntime."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from .task_models import TaskSpec

RuntimeSubmissionType = Literal[
    "user_turn",
    "start_task",
    "provide_task_input",
    "cancel_task",
    "query_runtime",
    "shutdown_runtime",
]


class RuntimeSubmissionBase(TypedDict, total=False):
    """所有 runtime submissions 的公共字段。"""

    submission_type: RuntimeSubmissionType
    session_id: str
    thread_id: str
    channel: str
    chat_id: str
    message_id: str
    metadata: dict[str, Any]


class UserTurnSubmission(RuntimeSubmissionBase, total=False):
    submission_type: Literal["user_turn"]
    content: str
    media: list[str]


class StartTaskSubmission(RuntimeSubmissionBase, total=False):
    submission_type: Literal["start_task"]
    task: TaskSpec


class ProvideTaskInputSubmission(RuntimeSubmissionBase, total=False):
    submission_type: Literal["provide_task_input"]
    task_id: str
    content: str
    origin_message_id: str


class CancelTaskSubmission(RuntimeSubmissionBase, total=False):
    submission_type: Literal["cancel_task"]
    task_id: str
    reason: str


class QueryRuntimeSubmission(RuntimeSubmissionBase, total=False):
    submission_type: Literal["query_runtime"]
    include_tasks: bool
    include_active_turn: bool


class ShutdownRuntimeSubmission(RuntimeSubmissionBase, total=False):
    submission_type: Literal["shutdown_runtime"]
    reason: str


RuntimeSubmission = (
    UserTurnSubmission
    | StartTaskSubmission
    | ProvideTaskInputSubmission
    | CancelTaskSubmission
    | QueryRuntimeSubmission
    | ShutdownRuntimeSubmission
)


__all__ = [
    "CancelTaskSubmission",
    "ProvideTaskInputSubmission",
    "QueryRuntimeSubmission",
    "RuntimeSubmission",
    "RuntimeSubmissionType",
    "ShutdownRuntimeSubmission",
    "StartTaskSubmission",
    "UserTurnSubmission",
]
