"""In-memory state store for the execution runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from emoticorebot.protocol.events import DeliveryTargetPayload
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec

from .state import ExecutionState


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


ExecutionResult = Literal["none", "success", "failed", "cancelled"]


@dataclass(slots=True)
class ExecutionRecord:
    task_id: str
    session_id: str
    turn_id: str | None
    job_id: str
    request: TaskRequestSpec
    title: str
    delivery_target: DeliveryTargetPayload
    origin_message: MessageRef | None = None
    state: ExecutionState = ExecutionState.RUNNING
    result: ExecutionResult = "none"
    state_version: int = 1
    summary: str = ""
    error: str = ""
    last_progress: str = ""
    progress: float | None = None
    next_step: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    job_kind: str = "execution_review"
    source_text: str = ""
    raw_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_log: list[dict[str, Any]] = field(default_factory=list)
    accepted: bool = False
    terminal_decision: str | None = None
    final_result_text: str = ""
    suppress_delivery: bool = False

    def touch(self) -> None:
        self.state_version += 1
        self.updated_at = utc_now()

    def mark_done(
        self,
        *,
        result: ExecutionResult,
        summary: str | None = None,
        error: str | None = None,
        decision: str | None = None,
        result_text: str | None = None,
    ) -> None:
        self.state = ExecutionState.DONE
        self.result = result
        if summary is not None:
            self.summary = str(summary or "").strip()
        if error is not None:
            self.error = str(error or "").strip()
        if decision is not None:
            self.terminal_decision = str(decision or "").strip() or None
        if result_text is not None:
            self.final_result_text = str(result_text or "").strip()
        self.ended_at = utc_now()
        self.touch()

    def append_trace(self, *, kind: str, message: str, data: dict[str, Any] | None = None, ts: str | None = None) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self.trace_log.append(
            {
                "trace_id": f"{self.task_id}:{len(self.trace_log) + 1}",
                "task_id": self.task_id,
                "kind": str(kind or "status").strip() or "status",
                "message": text,
                "ts": ts or utc_now(),
                "data": dict(data or {}),
            }
        )


class ExecutionStore:
    def __init__(self) -> None:
        self._tasks: dict[str, ExecutionRecord] = {}

    def add(self, task: ExecutionRecord) -> ExecutionRecord:
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> ExecutionRecord | None:
        return self._tasks.get(task_id)

    def all(self) -> list[ExecutionRecord]:
        return list(self._tasks.values())

    def for_session(self, session_id: str) -> list[ExecutionRecord]:
        wanted = str(session_id or "").strip()
        return [task for task in self._tasks.values() if task.session_id == wanted] if wanted else []

    def active_for_session(self, session_id: str) -> list[ExecutionRecord]:
        return [task for task in self.for_session(session_id) if task.state is not ExecutionState.DONE]

    def latest_for_session(self, session_id: str, *, include_terminal: bool = True) -> ExecutionRecord | None:
        tasks = self.for_session(session_id) if include_terminal else self.active_for_session(session_id)
        return max(tasks, key=lambda task: (task.updated_at, task.state_version)) if tasks else None

    def remove_session(self, session_id: str) -> None:
        for task in list(self.for_session(session_id)):
            self._tasks.pop(task.task_id, None)


__all__ = ["ExecutionRecord", "ExecutionStore", "utc_now"]
