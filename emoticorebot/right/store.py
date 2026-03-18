"""In-memory state store for the right-brain runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from emoticorebot.protocol.events import DeliveryTargetPayload
from emoticorebot.protocol.task_models import MessageRef, TaskRequestSpec, TaskStateSnapshot, TaskVisibleResult

from .state_machine import RightBrainState


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RightBrainRecord:
    task_id: str
    session_id: str
    turn_id: str | None
    job_id: str
    request: TaskRequestSpec
    title: str
    origin_message: MessageRef | None = None
    state: RightBrainState = RightBrainState.RUNNING
    result: TaskVisibleResult = "none"
    state_version: int = 1
    summary: str = ""
    error: str = ""
    last_progress: str = ""
    progress: float | None = None
    next_step: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    right_brain_strategy: str = "async"
    preferred_delivery_mode: str = "push"
    delivery_target: DeliveryTargetPayload | None = None
    job_kind: str = "execution_review"
    source_text: str = ""
    raw_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_log: list[dict[str, Any]] = field(default_factory=list)
    accepted: bool = False
    final_decision: str | None = None
    final_result_text: str = ""
    suppress_delivery: bool = False

    def snapshot(self) -> TaskStateSnapshot:
        return TaskStateSnapshot(
            task_id=self.task_id,
            state=self.state.value,
            result=self.result,
            state_version=self.state_version,
            title=self.title or None,
            summary=self.summary or None,
            error=self.error or None,
            last_progress=self.last_progress or None,
            updated_at=self.updated_at,
        )

    def touch(self) -> None:
        self.state_version += 1
        self.updated_at = utc_now()

    def mark_done(
        self,
        *,
        result: TaskVisibleResult,
        summary: str | None = None,
        error: str | None = None,
        decision: str | None = None,
        result_text: str | None = None,
    ) -> None:
        self.state = RightBrainState.DONE
        self.result = result
        if summary is not None:
            self.summary = str(summary or "").strip()
        if error is not None:
            self.error = str(error or "").strip()
        if decision is not None:
            self.final_decision = str(decision or "").strip() or None
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


class RightBrainStore:
    """Process-local store for right-brain records."""

    def __init__(self) -> None:
        self._tasks: dict[str, RightBrainRecord] = {}

    def add(self, task: RightBrainRecord) -> RightBrainRecord:
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> RightBrainRecord | None:
        return self._tasks.get(task_id)

    def require(self, task_id: str) -> RightBrainRecord:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"unknown right brain task: {task_id}")
        return task

    def all(self) -> list[RightBrainRecord]:
        return list(self._tasks.values())

    def for_session(self, session_id: str) -> list[RightBrainRecord]:
        wanted = str(session_id or "").strip()
        if not wanted:
            return []
        return [task for task in self._tasks.values() if task.session_id == wanted]

    def active_for_session(self, session_id: str) -> list[RightBrainRecord]:
        return [task for task in self.for_session(session_id) if task.state is not RightBrainState.DONE]

    def latest_for_session(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
    ) -> RightBrainRecord | None:
        tasks = self.for_session(session_id) if include_terminal else self.active_for_session(session_id)
        if not tasks:
            return None
        return max(tasks, key=lambda task: (task.updated_at, task.state_version))

    def waiting_for_session(self, session_id: str) -> RightBrainRecord | None:
        del session_id
        return None

    def remove_session(self, session_id: str) -> None:
        for task_id in [task.task_id for task in self.for_session(session_id)]:
            self._tasks.pop(task_id, None)


__all__ = ["RightBrainRecord", "RightBrainStore", "utc_now"]
