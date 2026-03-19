"""In-memory session models for the process-local dual-full-duplex runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from emoticorebot.protocol.task_models import MessageRef

TaskViewState = Literal["running", "done"]
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
    updated_at: str = ""
    trace: list[SessionTraceRecord] = field(default_factory=list)


@dataclass(slots=True)
class SessionContext:
    session_id: str
    channel_kind: str = "chat"
    session_summary: str = ""
    last_turn_id: str | None = None
    last_left_brain_instance_id: str | None = None
    active_input_stream_id: str | None = None
    active_input_stream_message: MessageRef | None = None
    active_input_stream_metadata: dict[str, Any] = field(default_factory=dict)
    active_input_stream_text: str = ""
    input_stream_commit_count: int = 0
    interrupted_input_stream_ids: set[str] = field(default_factory=set)
    active_reply_stream_id: str | None = None
    last_user_input: str = ""
    last_assistant_output: str = ""
    memory_snapshot: dict[str, Any] | None = None
    tasks: dict[str, SessionTaskView] = field(default_factory=dict)
    trace_cursor: dict[str, str] = field(default_factory=dict)
    active_task_ids: list[str] = field(default_factory=list)
    done_task_ids: list[str] = field(default_factory=list)
    archived: bool = False

    def rebuild_indexes(self) -> None:
        ordered = sorted(self.tasks.values(), key=lambda item: (item.updated_at, item.task_id))
        self.active_task_ids = [item.task_id for item in ordered if item.state == "running"]
        self.done_task_ids = [item.task_id for item in ordered if item.state == "done"]
        self.archived = (
            not self.active_task_ids
            and not self.active_reply_stream_id
            and not self.active_input_stream_id
        )


__all__ = [
    "SessionContext",
    "SessionTaskView",
    "SessionTraceRecord",
    "TaskViewResult",
    "TaskViewState",
]
