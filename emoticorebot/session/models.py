"""In-memory session models for the process-local dual-full-duplex runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TaskViewState = Literal["running", "waiting", "done"]
TaskViewResult = Literal["none", "success", "failed", "cancelled"]


@dataclass(slots=True)
class SessionTraceRecord:
    trace_id: str
    task_id: str
    kind: str
    message: str
    ts: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionTaskView:
    task_id: str
    title: str = ""
    request: str = ""
    state: TaskViewState = "running"
    result: TaskViewResult = "none"
    summary: str = ""
    latest_ask: str = ""
    latest_ask_field: str = ""
    latest_ask_at: str = ""
    updated_at: str = ""
    trace: list[SessionTraceRecord] = field(default_factory=list)


@dataclass(slots=True)
class SessionContext:
    session_id: str
    last_turn_id: str | None = None
    last_user_input: str = ""
    last_assistant_output: str = ""
    tasks: dict[str, SessionTaskView] = field(default_factory=dict)
    trace_cursor: dict[str, str] = field(default_factory=dict)
    active_task_ids: list[str] = field(default_factory=list)
    waiting_task_ids: list[str] = field(default_factory=list)
    done_task_ids: list[str] = field(default_factory=list)

    def rebuild_indexes(self) -> None:
        ordered = sorted(self.tasks.values(), key=lambda item: (item.updated_at, item.task_id))
        self.active_task_ids = [item.task_id for item in ordered if item.state == "running"]
        self.waiting_task_ids = [item.task_id for item in ordered if item.state == "waiting"]
        self.done_task_ids = [item.task_id for item in ordered if item.state == "done"]


__all__ = [
    "SessionContext",
    "SessionTaskView",
    "SessionTraceRecord",
    "TaskViewResult",
    "TaskViewState",
]
