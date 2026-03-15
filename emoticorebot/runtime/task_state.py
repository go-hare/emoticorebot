"""Serializable task state for lightweight execution wrappers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from emoticorebot.protocol.task_models import (
    ReviewItem,
    TaskControlState,
    TaskInputRequest,
    TaskLifecycleState,
    TaskResultStatus,
    TaskSpec,
    TaskState,
    TraceItem,
)
from emoticorebot.protocol.task_result import TaskExecutionResult


@dataclass
class RuntimeTaskState:
    """Serializable task snapshot kept separate from live task handles."""

    task_id: str
    title: str = ""
    params: TaskSpec = field(default_factory=dict)
    status: TaskLifecycleState = "running"
    summary: str = ""
    error: str = ""
    missing: list[str] = field(default_factory=list)
    input_request: TaskInputRequest | None = None
    stage_info: str = ""
    control_state: TaskControlState = "running"
    result_status: TaskResultStatus = "pending"
    analysis: str = ""
    pending_review: list[ReviewItem] = field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 1.0
    attempt_count: int = 1
    task_trace: list[TraceItem] = field(default_factory=list)

    def snapshot(self) -> TaskState:
        return {
            "invoked": True,
            "task_id": self.task_id,
            "title": self.title,
            "params": dict(self.params),
            "status": self.status,
            "result_status": self.result_status,
            "summary": self.summary,
            "error": self.error,
            "missing": list(self.missing),
            "input_request": dict(self.input_request or {}),
            "stage_info": self.stage_info,
            "control_state": self.control_state,
            "analysis": self.analysis,
            "pending_review": list(self.pending_review),
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "attempt_count": self.attempt_count,
            "task_trace": list(self.task_trace),
        }

    def sync_from_result(self, result: TaskExecutionResult | dict[str, Any]) -> None:
        """Copy structured executor output into the serializable task state."""
        if not isinstance(result, dict):
            return

        self.control_state = str(result.get("control_state", "running") or "running")
        self.result_status = str(result.get("status", "pending") or "pending")
        message = str(result.get("message", "") or "").strip()
        if message:
            self.summary = message
        self.analysis = str(result.get("analysis", "") or "")
        self.missing = list(result.get("missing", []) or [])
        self.pending_review = list(result.get("pending_review", []) or [])
        self.recommended_action = str(result.get("recommended_action", "") or "")
        self.task_trace = list(result.get("task_trace", []) or [])

        try:
            self.confidence = float(result.get("confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.confidence = 1.0

        try:
            self.attempt_count = int(result.get("attempt_count", 1) or 1)
        except (TypeError, ValueError):
            self.attempt_count = 1


__all__ = ["RuntimeTaskState"]
