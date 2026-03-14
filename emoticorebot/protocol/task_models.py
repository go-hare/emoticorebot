"""Typed task models shared by brain, runtime, executor, and reflection."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

TaskLifecycleState = Literal[
    "created",
    "running",
    "waiting_input",
    "blocked_input",
    "done",
    "failed",
    "cancelled",
]
TaskControlState = Literal["running", "waiting_input", "completed", "failed"]
TaskResultStatus = Literal["success", "partial", "pending", "failed"]
ReviewSeverity = Literal["low", "medium", "high"]


class ReviewItem(TypedDict, total=False):
    """待审核项。"""

    item_id: str
    label: str
    reason: str
    severity: ReviewSeverity
    blocking: bool
    required_action: str
    evidence: list[str]
    payload: dict[str, Any]


class TraceItem(TypedDict, total=False):
    """任务执行追踪中的单条记录。"""

    timestamp: str
    role: str
    type: str
    event: str
    phase: str
    node: str
    name: str
    tool: str
    tool_name: str
    tool_call_id: str
    content: str | list[dict[str, Any]]
    result: Any
    is_error: bool
    error: str
    args: dict[str, Any]
    payload: dict[str, Any]
    namespace: list[str]
    stream_mode: str
    trace_signature: str


class TaskInputRequest(TypedDict, total=False):
    """任务向用户追问时的输入请求。"""

    field: str
    question: str


class TaskSpec(TypedDict, total=False):
    """主脑委托给 runtime/executor 的结构化任务描述。"""

    task_id: str
    origin_message_id: str
    title: str
    request: str
    goal: str
    constraints: list[str]
    success_criteria: list[str]
    expected_output: str
    history: list[dict[str, Any]]
    task_context: dict[str, Any]
    history_context: str
    memory_bundle_ids: list[str]
    skill_hints: list[str]
    media: list[str]
    channel: str
    chat_id: str
    session_id: str


class TaskState(TypedDict, total=False):
    """运行时任务快照。"""

    invoked: bool
    task_id: str
    title: str
    params: TaskSpec
    status: TaskLifecycleState
    result_status: TaskResultStatus
    control_state: TaskControlState
    summary: str
    analysis: str
    error: str
    missing: list[str]
    input_request: TaskInputRequest
    stage_info: str
    pending_review: list[ReviewItem]
    recommended_action: str
    confidence: float
    attempt_count: int
    task_trace: list[TraceItem]


__all__ = [
    "ReviewItem",
    "ReviewSeverity",
    "TaskControlState",
    "TaskInputRequest",
    "TaskLifecycleState",
    "TaskResultStatus",
    "TaskSpec",
    "TaskState",
    "TraceItem",
]
