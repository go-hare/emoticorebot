"""Structured executor results returned to runtime."""

from __future__ import annotations

from typing import TypedDict

from .task_models import ReviewItem, TaskControlState, TaskResultStatus, TraceItem


class TaskExecutionResult(TypedDict, total=False):
    """task/executor 执行结束后返回的结构化结果。"""

    control_state: TaskControlState
    status: TaskResultStatus
    analysis: str
    message: str
    missing: list[str]
    pending_review: list[ReviewItem]
    recommended_action: str
    confidence: float
    attempt_count: int
    task_trace: list[TraceItem]


__all__ = ["TaskExecutionResult"]
