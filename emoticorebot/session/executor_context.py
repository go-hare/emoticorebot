"""Shared helpers for persisted executor context summaries."""

from __future__ import annotations


def compact_text(text: str, limit: int = 400) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."


def build_executor_context(message: dict[str, object], *, summary_limit: int = 500) -> str:
    execution = message.get("execution") if isinstance(message.get("execution"), dict) else {}
    summary = compact_text(str((execution or {}).get("summary", "")).strip(), limit=summary_limit)
    if summary:
        return summary

    control_state = compact_text(str((execution or {}).get("control_state", "")).strip(), limit=24)
    status = compact_text(str((execution or {}).get("status", "")).strip(), limit=24)
    confidence = float((execution or {}).get("confidence", 0.0) or 0.0)
    recommended_action = compact_text(str((execution or {}).get("recommended_action", "")).strip(), limit=36)
    missing = [
        str(item).strip()
        for item in ((execution or {}).get("missing", []) if isinstance((execution or {}).get("missing", []), list) else [])
        if str(item).strip()
    ]

    parts: list[str] = []
    state_label = control_state or "idle"
    status_label = status or "none"
    label = (
        f"[Executor|{state_label}|{status_label}|{confidence:.2f}]"
        if confidence > 0
        else f"[Executor|{state_label}|{status_label}]"
    )
    parts.append(label)
    if recommended_action:
        parts.append(f"建议动作: {recommended_action}")
    if missing:
        parts.append(f"缺失信息: {', '.join(missing[:5])}")
    return "；".join(parts)


__all__ = ["build_executor_context", "compact_text"]
