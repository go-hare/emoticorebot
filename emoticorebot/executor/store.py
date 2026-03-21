"""In-memory state store for the executor runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from typing import Any

from emoticorebot.protocol.events import DeliveryTargetPayload
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec

from .state import ExecutorState


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


ExecutorResult = Literal["none", "success", "failed", "cancelled"]


@dataclass(slots=True)
class ExecutorRecord:
    task_id: str
    session_id: str
    turn_id: str | None
    job_id: str
    request: TaskRequestSpec
    title: str
    delivery_target: DeliveryTargetPayload
    origin_message: MessageRef | None = None
    state: ExecutorState = ExecutorState.RUNNING
    result: ExecutorResult = "none"
    state_version: int = 1
    summary: str = ""
    error: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    job_kind: str = "execution_review"
    source_text: str = ""
    raw_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_log: list[dict[str, Any]] = field(default_factory=list)
    terminal_decision: str | None = None
    final_result_text: str = ""
    suppress_delivery: bool = False

    def touch(self) -> None:
        self.state_version += 1
        self.updated_at = utc_now()

    def mark_done(
        self,
        *,
        result: ExecutorResult,
        summary: str | None = None,
        error: str | None = None,
        decision: str | None = None,
        result_text: str | None = None,
    ) -> None:
        self.state = ExecutorState.DONE
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


class ExecutorStore:
    """Process-local store for executor records."""

    def __init__(self) -> None:
        self._tasks: dict[str, ExecutorRecord] = {}

    def add(self, task: ExecutorRecord) -> ExecutorRecord:
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> ExecutorRecord | None:
        return self._tasks.get(task_id)

    def require(self, task_id: str) -> ExecutorRecord:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"unknown executor task: {task_id}")
        return task

    def all(self) -> list[ExecutorRecord]:
        return list(self._tasks.values())

    def for_session(self, session_id: str) -> list[ExecutorRecord]:
        wanted = str(session_id or "").strip()
        if not wanted:
            return []
        return [task for task in self._tasks.values() if task.session_id == wanted]

    def active_for_session(self, session_id: str) -> list[ExecutorRecord]:
        return [task for task in self.for_session(session_id) if task.state is not ExecutorState.DONE]

    def latest_for_session(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
    ) -> ExecutorRecord | None:
        tasks = self.for_session(session_id) if include_terminal else self.active_for_session(session_id)
        if not tasks:
            return None
        return max(tasks, key=lambda task: (task.updated_at, task.state_version))

    def remove_session(self, session_id: str) -> None:
        for task_id in [task.task_id for task in self.for_session(session_id)]:
            self._tasks.pop(task_id, None)


__all__ = ["ExecutorRecord", "ExecutorStore", "utc_now"]
