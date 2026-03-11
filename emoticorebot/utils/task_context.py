"""Small helpers for rendering persisted task context."""

from __future__ import annotations

from typing import Any


def build_task_context(payload: dict[str, Any] | None) -> str:
    task = payload.get("task") if isinstance(payload, dict) and isinstance(payload.get("task"), dict) else {}
    if not task:
        return ""

    title = str(task.get("title", "") or task.get("goal", "") or task.get("task_id", "")).strip()
    status = str(task.get("status", "") or "").strip()
    summary = str(task.get("summary", "") or task.get("analysis", "") or "").strip()
    missing = [str(item).strip() for item in list(task.get("missing", []) or []) if str(item).strip()]

    parts: list[str] = []
    if title:
        parts.append(f"任务: {title}")
    if status:
        parts.append(f"状态: {status}")
    if summary:
        parts.append(f"总结: {summary}")
    if missing:
        parts.append("待补充: " + "、".join(missing[:5]))
    return " | ".join(parts)


__all__ = ["build_task_context"]
