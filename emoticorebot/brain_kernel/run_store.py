"""Run is the only task primitive inside the standalone brain service."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from .memory import make_id, now_iso


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    created = "created"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Run(BaseModel):
    id: str = Field(default_factory=lambda: make_id("run"))
    agent_id: str
    conversation_id: str
    goal: str
    status: RunStatus = RunStatus.created
    background: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    current_tool: str = ""
    result_summary: str = ""
    error: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    completed_at: str = ""


class RunStore:
    """Track many runs in one brain without introducing an executor layer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, Run] = {}

    def create_run(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        goal: str,
        background: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Run:
        run = Run(
            agent_id=agent_id,
            conversation_id=conversation_id,
            goal=goal,
            background=background,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._runs[run.id] = run
        return run.model_copy(deep=True)

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run else None

    def list_runs(
        self,
        *,
        agent_id: str | None = None,
        conversation_id: str | None = None,
        statuses: set[RunStatus] | None = None,
    ) -> list[Run]:
        with self._lock:
            rows = list(self._runs.values())

        result: list[Run] = []
        for run in rows:
            if agent_id and run.agent_id != agent_id:
                continue
            if conversation_id and run.conversation_id != conversation_id:
                continue
            if statuses and run.status not in statuses:
                continue
            result.append(run.model_copy(deep=True))

        result.sort(key=lambda item: item.updated_at, reverse=True)
        return result

    def list_active_runs(self, agent_id: str | None = None, conversation_id: str | None = None) -> list[Run]:
        return self.list_runs(
            agent_id=agent_id,
            conversation_id=conversation_id,
            statuses={RunStatus.created, RunStatus.running},
        )

    def update_run(self, run_id: str, **changes: Any) -> Run:
        with self._lock:
            run = self._runs[run_id]
            payload = run.model_dump()
            payload.update(changes)
            payload["updated_at"] = now_iso()
            updated = Run.model_validate(payload)
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def mark_running(self, run_id: str, current_tool: str = "") -> Run:
        changes: dict[str, Any] = {"status": RunStatus.running}
        if current_tool:
            changes["current_tool"] = current_tool
        return self.update_run(run_id, **changes)

    def finish_run(self, run_id: str, result_summary: str = "") -> Run:
        return self.update_run(
            run_id,
            status=RunStatus.completed,
            result_summary=result_summary,
            current_tool="",
            completed_at=utc_now().isoformat(),
        )

    def fail_run(self, run_id: str, error: str) -> Run:
        return self.update_run(
            run_id,
            status=RunStatus.failed,
            error=error,
            current_tool="",
            completed_at=utc_now().isoformat(),
        )

    def cancel_run(self, run_id: str, reason: str = "") -> Run:
        return self.update_run(
            run_id,
            status=RunStatus.cancelled,
            error=reason,
            current_tool="",
            completed_at=utc_now().isoformat(),
        )
