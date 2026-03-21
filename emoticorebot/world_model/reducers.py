"""Pure reducers for updating the shared world model."""

from __future__ import annotations

from typing import Any

from emoticorebot.world_model.projectors import artifact_refs_from_blocks, merge_unique_strings, project_task_from_executor_record
from emoticorebot.world_model.schema import WorldCheckRecord, WorldCurrentCheck, WorldModel, WorldTask, utc_now


def touch_current_topic(model: WorldModel, topic: str) -> WorldModel:
    text = str(topic or "").strip()
    if not text:
        return model
    model.current_topic = text
    model.updated_at = utc_now()
    return model


def set_current_task(model: WorldModel, task: WorldTask) -> WorldTask:
    existing = model.current_task
    if existing is None or existing.task_id != task.task_id:
        model.current_task = task
        model.updated_at = str(task.updated_at or "").strip() or utc_now()
        return task

    if task.goal:
        existing.goal = task.goal
    if task.status:
        existing.status = task.status
    if task.summary:
        existing.summary = task.summary
    if task.mainline:
        existing.mainline = list(task.mainline)
    if task.current_stage not in (None, "", []):
        existing.current_stage = task.current_stage
    if task.current_batch_id:
        existing.current_batch_id = task.current_batch_id
    if task.current_checks:
        existing.current_checks = [WorldCurrentCheck.from_dict(item.to_dict(), fallback_index=index) for index, item in enumerate(task.current_checks)]
    if task.last_result:
        existing.last_result = task.last_result
    if task.artifacts:
        existing.artifacts = merge_unique_strings(existing.artifacts, task.artifacts)
    if task.created_at:
        existing.created_at = str(existing.created_at or "").strip() or task.created_at
    existing.updated_at = str(task.updated_at or "").strip() or utc_now()
    model.updated_at = existing.updated_at
    return existing


def clear_current_task(model: WorldModel, task_id: str = "") -> WorldModel:
    wanted = str(task_id or "").strip()
    if not wanted:
        model.current_task = None
        model.updated_at = utc_now()
        return model
    current = model.current_task
    if current is None:
        return model
    if current.task_id != wanted:
        return model
    model.current_task = None
    model.updated_at = utc_now()
    return model


def apply_executor_terminal(
    model: WorldModel,
    *,
    task_id: str,
    record: Any | None = None,
    summary: str = "",
    result_text: str = "",
    terminal_status: str = "",
    artifacts: list[Any] | None = None,
) -> WorldModel:
    task = model.task(task_id)
    if task is not None:
        artifact_refs = artifact_refs_from_blocks(artifacts)
        if artifact_refs:
            task.artifacts = merge_unique_strings(task.artifacts, artifact_refs)
        text = str(summary or result_text or "").strip()
        resolved_status = str(terminal_status or "").strip() or "success"
        if text:
            task.last_result = text
            task.summary = text
            _mark_current_checks_terminal(task, status=resolved_status, result=text, artifacts=task.artifacts)
            _append_check_history(task, result=text, artifacts=task.artifacts)
        task.touch()
        model.updated_at = task.updated_at
    return model


def _current_check_titles(task: WorldTask, record: Any | None) -> list[str]:
    checks = list(task.current_checks or [])
    if checks:
        return [str(item.title or "").strip() for item in checks if str(item.title or "").strip()]
    if record is not None and getattr(record, "request", None) is not None:
        text = str(getattr(record.request, "request", "") or "").strip()
        return [text] if text else []
    return []


def _mark_current_checks_terminal(
    task: WorldTask,
    *,
    status: str,
    result: str,
    artifacts: list[str] | None = None,
) -> None:
    artifact_list = list(artifacts or [])
    for item in task.current_checks:
        if item.status in {"success", "failed", "cancelled"}:
            continue
        item.status = status
        if status == "failed":
            item.error = result
        else:
            item.result = result
        if artifact_list:
            item.artifacts = merge_unique_strings(item.artifacts, artifact_list)
        item.touch()


def _append_check_history(task: WorldTask, *, result: str, artifacts: list[str] | None = None) -> None:
    result_text = str(result or "").strip()
    check_titles = _current_check_titles(task, None)
    if not check_titles or not result_text:
        return
    artifact_list = list(artifacts or [])
    for check_text in check_titles:
        latest = task.check_history[-1] if task.check_history else None
        if latest is not None and latest.check == check_text and latest.result == result_text:
            latest.artifacts = merge_unique_strings(latest.artifacts, artifact_list)
            continue
        task.check_history.append(
            WorldCheckRecord(
                check=check_text,
                result=result_text,
                artifacts=artifact_list,
            )
        )


__all__ = [
    "apply_executor_terminal",
    "clear_current_task",
    "set_current_task",
    "touch_current_topic",
]
