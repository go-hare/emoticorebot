"""Runtime-owned task store for the v3 scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from emoticorebot.protocol.events import TaskResultReportPayload
from emoticorebot.protocol.task_models import (
    AgentRole,
    InputRequest,
    MessageRef,
    PlanStep,
    ReviewPolicy,
    ReviewItem,
    TaskRequestSpec,
    TaskStateSnapshot,
)
from emoticorebot.runtime.state_machine import TaskStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RuntimeTaskRecord:
    task_id: str
    session_id: str
    turn_id: str | None
    request: TaskRequestSpec
    origin_message: MessageRef | None
    title: str
    status: TaskStatus = TaskStatus.CREATED
    state_version: int = 1
    summary: str = ""
    error: str = ""
    assignee: AgentRole | None = None
    plan_id: str | None = None
    plan_steps: list[PlanStep] = field(default_factory=list)
    review_required: bool = False
    review_policy: ReviewPolicy = "skip"
    last_progress: str = ""
    input_request: InputRequest | None = None
    updated_at: str = field(default_factory=utc_now)
    current_assignment_id: str | None = None
    current_review_id: str | None = None
    latest_result: TaskResultReportPayload | None = None
    latest_rejection_reason: str | None = None
    latest_findings: list[ReviewItem] = field(default_factory=list)
    suppress_delivery: bool = False

    def snapshot(self) -> TaskStateSnapshot:
        return TaskStateSnapshot(
            task_id=self.task_id,
            state_version=self.state_version,
            status=self.status.value,
            title=self.title,
            summary=self.summary or None,
            error=self.error or None,
            assignee=self.assignee,
            plan_id=self.plan_id,
            review_required=self.review_required,
            last_progress=self.last_progress or None,
            input_request=self.input_request,
            updated_at=self.updated_at,
        )

    def touch(self) -> None:
        self.state_version += 1
        self.updated_at = utc_now()


class TaskStore:
    """In-memory task store used by the new runtime scheduler."""

    def __init__(self) -> None:
        self._tasks: dict[str, RuntimeTaskRecord] = {}

    def add(self, task: RuntimeTaskRecord) -> RuntimeTaskRecord:
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> RuntimeTaskRecord | None:
        return self._tasks.get(task_id)

    def require(self, task_id: str) -> RuntimeTaskRecord:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        return task

    def all(self) -> list[RuntimeTaskRecord]:
        return list(self._tasks.values())

    def for_session(self, session_id: str) -> list[RuntimeTaskRecord]:
        wanted = str(session_id or "").strip()
        if not wanted:
            return []
        return [task for task in self._tasks.values() if task.session_id == wanted]

    def active_for_session(self, session_id: str) -> list[RuntimeTaskRecord]:
        return [
            task
            for task in self.for_session(session_id)
            if task.status not in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ARCHIVED}
        ]

    def latest_for_session(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
    ) -> RuntimeTaskRecord | None:
        tasks = self.for_session(session_id) if include_terminal else self.active_for_session(session_id)
        if not tasks:
            return None
        return max(tasks, key=lambda task: (task.updated_at, task.state_version))

    def waiting_for_session(self, session_id: str) -> RuntimeTaskRecord | None:
        for task in reversed(self.for_session(session_id)):
            if task.status is TaskStatus.WAITING_INPUT:
                return task
        return None

    def remove_session(self, session_id: str) -> None:
        for task_id in [task.task_id for task in self.for_session(session_id)]:
            self._tasks.pop(task_id, None)


__all__ = ["RuntimeTaskRecord", "TaskStore", "utc_now"]
