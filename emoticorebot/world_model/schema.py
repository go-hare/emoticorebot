"""Schema objects for the shared world model runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = str(entry or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def normalize_mainline(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    steps: list[Any] = []
    for entry in value:
        if isinstance(entry, list):
            group = normalize_string_list(entry)
            if group:
                steps.append(group)
            continue
        text = str(entry or "").strip()
        if text:
            steps.append(text)
    return steps


def normalize_stage(value: Any) -> str | list[str] | None:
    if isinstance(value, list):
        group = normalize_string_list(value)
        return group or None
    text = str(value or "").strip()
    return text or None


def _check_text(value: Any) -> str:
    if isinstance(value, dict):
        text = str(value.get("title", "") or value.get("check", "") or "").strip()
        return text
    return str(value or "").strip()


@dataclass(slots=True)
class WorldCheckRecord:
    check: str
    result: str = ""
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "check": str(self.check or "").strip(),
            "result": str(self.result or "").strip(),
            "artifacts": list(self.artifacts or []),
            "created_at": str(self.created_at or "").strip(),
        }
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldCheckRecord":
        return cls(
            check=str(data.get("check", "") or "").strip(),
            result=str(data.get("result", "") or "").strip(),
            artifacts=normalize_string_list(data.get("artifacts")),
            created_at=str(data.get("created_at", "") or "").strip() or utc_now(),
        )


@dataclass(slots=True)
class WorldCurrentCheck:
    check_id: str
    title: str
    status: str = "pending"
    result: str = ""
    error: str = ""
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "check_id": str(self.check_id or "").strip(),
            "title": str(self.title or "").strip(),
            "status": str(self.status or "").strip() or "pending",
            "result": str(self.result or "").strip(),
            "error": str(self.error or "").strip(),
            "artifacts": list(self.artifacts or []),
            "created_at": str(self.created_at or "").strip(),
            "updated_at": str(self.updated_at or "").strip(),
        }
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, fallback_index: int = 0) -> "WorldCurrentCheck":
        title = _check_text(data)
        check_id = str(data.get("check_id", "") or "").strip() or f"check_{fallback_index + 1:03d}"
        return cls(
            check_id=check_id,
            title=title,
            status=str(data.get("status", "") or "").strip() or "pending",
            result=str(data.get("result", "") or "").strip(),
            error=str(data.get("error", "") or "").strip(),
            artifacts=normalize_string_list(data.get("artifacts")),
            created_at=str(data.get("created_at", "") or "").strip() or utc_now(),
            updated_at=str(data.get("updated_at", "") or "").strip() or utc_now(),
        )


def normalize_current_checks(value: Any) -> list["WorldCurrentCheck"]:
    if not isinstance(value, list):
        return []
    items: list[WorldCurrentCheck] = []
    for index, entry in enumerate(value):
        if isinstance(entry, WorldCurrentCheck):
            check = WorldCurrentCheck.from_dict(entry.to_dict(), fallback_index=index)
            if check.title:
                items.append(check)
            continue
        if isinstance(entry, dict):
            check = WorldCurrentCheck.from_dict(entry, fallback_index=index)
        else:
            title = _check_text(entry)
            if not title:
                continue
            check = WorldCurrentCheck(
                check_id=f"check_{index + 1:03d}",
                title=title,
            )
        if check.title:
            items.append(check)
    return items


@dataclass(slots=True)
class WorldTask:
    task_id: str
    goal: str = ""
    status: str = "running"
    summary: str = ""
    mainline: list[Any] = field(default_factory=list)
    current_stage: str | list[str] | None = None
    current_batch_id: str = ""
    current_checks: list[WorldCurrentCheck] = field(default_factory=list)
    last_result: str = ""
    check_history: list[WorldCheckRecord] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.mainline = normalize_mainline(self.mainline)
        self.current_stage = normalize_stage(self.current_stage)
        self.current_checks = normalize_current_checks(self.current_checks)
        self.artifacts = normalize_string_list(self.artifacts)
        self.status = str(self.status or "").strip() or "running"
        self.summary = str(self.summary or "").strip()
        self.current_batch_id = str(self.current_batch_id or "").strip()
        self.last_result = str(self.last_result or "").strip()

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": str(self.task_id or "").strip(),
            "goal": str(self.goal or "").strip(),
            "status": str(self.status or "").strip() or "running",
            "summary": str(self.summary or "").strip(),
            "mainline": normalize_mainline(self.mainline),
            "current_stage": normalize_stage(self.current_stage),
            "current_batch_id": str(self.current_batch_id or "").strip(),
            "current_checks": [item.to_dict() for item in self.current_checks if item.to_dict()],
            "last_result": str(self.last_result or "").strip(),
            "check_history": [item.to_dict() for item in self.check_history if item.to_dict()],
            "artifacts": list(self.artifacts or []),
            "created_at": str(self.created_at or "").strip(),
            "updated_at": str(self.updated_at or "").strip(),
        }
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldTask":
        history: list[WorldCheckRecord] = []
        for item in list(data.get("check_history", []) or []):
            if isinstance(item, dict):
                record = WorldCheckRecord.from_dict(item)
                if record.check:
                    history.append(record)
        task_id = str(data.get("task_id", "") or "").strip()
        return cls(
            task_id=task_id,
            goal=str(data.get("goal", "") or "").strip(),
            status=str(data.get("status", "") or "").strip() or "running",
            summary=str(data.get("summary", "") or "").strip(),
            mainline=normalize_mainline(data.get("mainline")),
            current_stage=normalize_stage(data.get("current_stage")),
            current_batch_id=str(data.get("current_batch_id", "") or "").strip(),
            current_checks=normalize_current_checks(data.get("current_checks")),
            last_result=str(data.get("last_result", "") or "").strip(),
            check_history=history,
            artifacts=normalize_string_list(data.get("artifacts")),
            created_at=str(data.get("created_at", "") or "").strip() or utc_now(),
            updated_at=str(data.get("updated_at", "") or "").strip() or utc_now(),
        )


@dataclass(slots=True)
class WorldModel:
    session_id: str
    schema_version: str = "world_model.single_task.v1"
    updated_at: str = field(default_factory=utc_now)
    current_topic: str = ""
    current_task: WorldTask | None = None

    def task(self, task_id: str) -> WorldTask | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        if self.current_task is None:
            return None
        return self.current_task if self.current_task.task_id == wanted else None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": str(self.schema_version or "").strip() or "world_model.single_task.v1",
            "session_id": str(self.session_id or "").strip(),
            "updated_at": str(self.updated_at or "").strip(),
            "current_topic": str(self.current_topic or "").strip(),
            "current_task": self.current_task.to_dict() if self.current_task is not None else None,
        }
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, session_id: str = "") -> "WorldModel":
        current_task: WorldTask | None = None
        raw_task = data.get("current_task")
        if isinstance(raw_task, dict):
            task = WorldTask.from_dict(raw_task)
            if task.task_id:
                current_task = task
        resolved_session_id = str(data.get("session_id", "") or session_id or "").strip()
        return cls(
            session_id=resolved_session_id,
            schema_version=str(data.get("schema_version", "") or "").strip() or "world_model.single_task.v1",
            updated_at=str(data.get("updated_at", "") or "").strip() or utc_now(),
            current_topic=str(data.get("current_topic", "") or "").strip(),
            current_task=current_task,
        )


__all__ = [
    "WorldCurrentCheck",
    "WorldCheckRecord",
    "WorldModel",
    "WorldTask",
    "normalize_current_checks",
    "normalize_mainline",
    "normalize_stage",
    "normalize_string_list",
    "utc_now",
]
