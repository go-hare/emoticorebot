"""Shared helpers for persisted task context summaries."""

from __future__ import annotations


def compact_text(text: str, limit: int = 400) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."


def build_task_context(message: dict[str, object], *, summary_limit: int = 500) -> str:
    task = message.get("task") if isinstance(message.get("task"), dict) else {}
    summary = compact_text(str((task or {}).get("summary", "")).strip(), limit=summary_limit)
    if summary:
        return summary

    control_state = compact_text(str((task or {}).get("control_state", "")).strip(), limit=24)
    status = compact_text(str((task or {}).get("status", "")).strip(), limit=24)
    confidence = float((task or {}).get("confidence", 0.0) or 0.0)
    recommended_action = compact_text(str((task or {}).get("recommended_action", "")).strip(), limit=36)
    missing = [
        str(item).strip()
        for item in ((task or {}).get("missing", []) if isinstance((task or {}).get("missing", []), list) else [])
        if str(item).strip()
    ]

    parts: list[str] = []
    state_label = control_state or "idle"
    status_label = status or "none"
    label = (
        f"[Task|{state_label}|{status_label}|{confidence:.2f}]"
        if confidence > 0
        else f"[Task|{state_label}|{status_label}]"
    )
    parts.append(label)
    if recommended_action:
        parts.append(f"建议动作: {recommended_action}")
    if missing:
        parts.append(f"缺失信息: {', '.join(missing[:5])}")
    return "；".join(parts)


__all__ = ["build_task_context", "compact_text"]
