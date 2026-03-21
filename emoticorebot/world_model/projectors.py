"""Projection helpers that map runtime payloads into world-model task fields."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from emoticorebot.world_model.schema import (
    WorldTask,
    normalize_current_checks,
    normalize_mainline,
    normalize_stage,
    normalize_string_list,
    utc_now,
)


def merge_unique_strings(*parts: Sequence[str]) -> list[str]:
    merged: list[str] = []
    for part in parts:
        for entry in part:
            text = str(entry or "").strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def artifact_refs_from_blocks(blocks: Sequence[Any] | None) -> list[str]:
    refs: list[str] = []
    for block in list(blocks or []):
        if not hasattr(block, "type") and not isinstance(block, Mapping):
            continue
        get = block.get if isinstance(block, Mapping) else lambda key, default=None: getattr(block, key, default)
        for key in ("path", "url", "name", "text"):
            text = str(get(key, "") or "").strip()
            if text and text not in refs:
                refs.append(text)
                break
    return refs


def build_task_blueprint(request: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(request or {})
    context = payload.get("context")
    if not isinstance(context, Mapping):
        context = {}

    request_text = str(payload.get("request_text", "") or payload.get("source_text", "") or "").strip()
    goal = (
        str(payload.get("goal", "") or "").strip()
        or str(context.get("goal", "") or "").strip()
        or request_text
    )
    current_checks = normalize_current_checks(payload.get("current_checks"))
    if not current_checks:
        current_checks = normalize_current_checks(context.get("current_checks"))
    if not current_checks and request_text:
        current_checks = normalize_current_checks([request_text])

    mainline = normalize_mainline(payload.get("mainline"))
    if not mainline:
        mainline = normalize_mainline(context.get("mainline"))
    if not mainline and goal:
        mainline = [goal]

    current_stage = normalize_stage(payload.get("current_stage"))
    if current_stage is None:
        current_stage = normalize_stage(context.get("current_stage"))
    if current_stage is None:
        if len(current_checks) == 1:
            current_stage = current_checks[0].title
        elif len(current_checks) > 1:
            current_stage = [item.title for item in current_checks]

    blueprint = {
        "goal": goal,
        "status": str(payload.get("status", "") or "running").strip() or "running",
        "summary": str(payload.get("summary", "") or goal).strip(),
        "mainline": mainline,
        "current_stage": current_stage,
        "current_batch_id": str(payload.get("current_batch_id", "") or payload.get("job_id", "") or "").strip(),
        "current_checks": current_checks,
        "artifacts": normalize_string_list(context.get("artifacts")),
        "created_at": str(context.get("created_at", "") or "").strip() or utc_now(),
    }
    return {key: value for key, value in blueprint.items() if value not in ("", None, [], {})}


def project_task_from_blueprint(task_id: str, blueprint: Mapping[str, Any] | None) -> WorldTask:
    payload = dict(blueprint or {})
    goal = str(payload.get("goal", "") or "").strip()
    mainline = normalize_mainline(payload.get("mainline"))
    if not mainline and goal:
        mainline = [goal]
    current_checks = normalize_current_checks(payload.get("current_checks"))
    current_stage = normalize_stage(payload.get("current_stage"))
    if current_stage is None:
        if len(current_checks) == 1:
            current_stage = current_checks[0].title
        elif len(current_checks) > 1:
            current_stage = [item.title for item in current_checks]
    created_at = str(payload.get("created_at", "") or "").strip() or utc_now()
    updated_at = str(payload.get("updated_at", "") or created_at).strip() or created_at
    return WorldTask(
        task_id=str(task_id or "").strip(),
        goal=goal,
        status=str(payload.get("status", "") or "").strip() or "running",
        summary=str(payload.get("summary", "") or goal).strip(),
        mainline=mainline,
        current_stage=current_stage,
        current_batch_id=str(payload.get("current_batch_id", "") or "").strip(),
        current_checks=current_checks,
        last_result=str(payload.get("last_result", "") or "").strip(),
        artifacts=normalize_string_list(payload.get("artifacts")),
        created_at=created_at,
        updated_at=updated_at,
    )


def project_task_from_executor_record(record: Any, *, blueprint: Mapping[str, Any] | None = None) -> WorldTask:
    payload = dict(blueprint or {})
    request = getattr(record, "request", None)
    request_text = str(getattr(request, "request", "") or "").strip()
    goal = (
        str(payload.get("goal", "") or "").strip()
        or str(getattr(request, "goal", "") or "").strip()
        or str(getattr(record, "title", "") or "").strip()
        or request_text
    )
    mainline = normalize_mainline(payload.get("mainline"))
    if not mainline and goal:
        mainline = [goal]
    current_checks = normalize_current_checks(payload.get("current_checks"))
    if not current_checks and request_text:
        current_checks = normalize_current_checks([request_text])
    current_stage = normalize_stage(payload.get("current_stage"))
    if current_stage is None:
        if len(current_checks) == 1:
            current_stage = current_checks[0].title
        elif len(current_checks) > 1:
            current_stage = [item.title for item in current_checks]

    artifacts = merge_unique_strings(
        normalize_string_list(payload.get("artifacts")),
        normalize_string_list(getattr(record, "metadata", {}).get("artifacts") if isinstance(getattr(record, "metadata", {}), dict) else []),
    )
    created_at = str(getattr(record, "created_at", "") or payload.get("created_at", "") or "").strip() or utc_now()
    updated_at = str(getattr(record, "updated_at", "") or created_at).strip() or created_at
    last_result = str(getattr(record, "summary", "") or "").strip()
    return WorldTask(
        task_id=str(getattr(record, "task_id", "") or "").strip(),
        goal=goal,
        status="running",
        summary=last_result or goal,
        mainline=mainline,
        current_stage=current_stage,
        current_batch_id=str(getattr(record, "job_id", "") or payload.get("current_batch_id", "") or "").strip(),
        current_checks=current_checks,
        last_result=last_result,
        artifacts=artifacts,
        created_at=created_at,
        updated_at=updated_at,
    )


__all__ = [
    "artifact_refs_from_blocks",
    "build_task_blueprint",
    "merge_unique_strings",
    "project_task_from_blueprint",
    "project_task_from_executor_record",
]
