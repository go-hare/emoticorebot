"""Live task handles used by execution wrappers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Protocol

from emoticorebot.protocol.task_models import TaskInputRequest, TaskLifecycleState, TaskSpec, TaskState
from emoticorebot.runtime.task_state import RuntimeTaskState

from emoticorebot.protocol.task_result import TaskExecutionResult


class TaskRuntime(Protocol):
    async def report_progress(self, task: "RunningTask", message: str, **payload: Any) -> None: ...


TaskWorker = Callable[["RunningTask", TaskRuntime], Awaitable[Any]]


@dataclass
class RunningTask:
    """Live execution instance that keeps handles separate from task state."""

    task_id: str
    title: str = ""
    params: TaskSpec = field(default_factory=dict)
    worker: TaskWorker | None = None
    runner: asyncio.Task | None = None
    input_fut: asyncio.Future | None = None
    result: Any = None
    started_at: str = ""
    state: RuntimeTaskState = field(init=False)

    def __post_init__(self) -> None:
        params = dict(self.params or {})
        title = str(self.title or "").strip()
        self.title = title
        self.params = params
        self.state = RuntimeTaskState(task_id=self.task_id, title=title, params=params)

    @property
    def status(self) -> TaskLifecycleState:
        return self.state.status

    @status.setter
    def status(self, value: TaskLifecycleState) -> None:
        self.state.status = value

    @property
    def summary(self) -> str:
        return self.state.summary

    @summary.setter
    def summary(self, value: str) -> None:
        self.state.summary = str(value or "")

    @property
    def error(self) -> str:
        return self.state.error

    @error.setter
    def error(self, value: str) -> None:
        self.state.error = str(value or "")

    @property
    def missing(self) -> list[str]:
        return self.state.missing

    @missing.setter
    def missing(self, value: list[str]) -> None:
        self.state.missing = list(value or [])

    @property
    def input_request(self) -> TaskInputRequest | None:
        return self.state.input_request

    @input_request.setter
    def input_request(self, value: TaskInputRequest | None) -> None:
        self.state.input_request = dict(value or {}) or None

    @property
    def stage_info(self) -> str:
        return self.state.stage_info

    @stage_info.setter
    def stage_info(self, value: str) -> None:
        self.state.stage_info = str(value or "")

    @property
    def control_state(self) -> str:
        return self.state.control_state

    @control_state.setter
    def control_state(self, value: str) -> None:
        self.state.control_state = str(value or "running")

    @property
    def result_status(self) -> str:
        return self.state.result_status

    @result_status.setter
    def result_status(self, value: str) -> None:
        self.state.result_status = str(value or "pending")

    @property
    def analysis(self) -> str:
        return self.state.analysis

    @analysis.setter
    def analysis(self, value: str) -> None:
        self.state.analysis = str(value or "")

    @property
    def pending_review(self) -> list[dict[str, Any]]:
        return self.state.pending_review

    @pending_review.setter
    def pending_review(self, value: list[dict[str, Any]]) -> None:
        self.state.pending_review = list(value or [])

    @property
    def recommended_action(self) -> str:
        return self.state.recommended_action

    @recommended_action.setter
    def recommended_action(self, value: str) -> None:
        self.state.recommended_action = str(value or "")

    @property
    def confidence(self) -> float:
        return self.state.confidence

    @confidence.setter
    def confidence(self, value: float) -> None:
        try:
            self.state.confidence = float(value)
        except (TypeError, ValueError):
            self.state.confidence = 1.0

    @property
    def attempt_count(self) -> int:
        return self.state.attempt_count

    @attempt_count.setter
    def attempt_count(self, value: int) -> None:
        try:
            self.state.attempt_count = int(value)
        except (TypeError, ValueError):
            self.state.attempt_count = 1

    @property
    def task_trace(self) -> list[dict[str, Any]]:
        return self.state.task_trace

    @task_trace.setter
    def task_trace(self, value: list[dict[str, Any]]) -> None:
        self.state.task_trace = list(value or [])

    def mark_started(self) -> None:
        self.started_at = datetime.now(UTC).isoformat()

    def snapshot(self) -> TaskState:
        return self.state.snapshot()

    def sync_from_result(self, result: TaskExecutionResult | dict[str, Any]) -> None:
        self.state.sync_from_result(result)


__all__ = ["RunningTask", "TaskRuntime", "TaskWorker"]
