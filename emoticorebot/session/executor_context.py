"""Shared helpers for persisted executor context summaries."""

from __future__ import annotations


def compact_text(text: str, limit: int = 400) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."


def build_executor_context(message: dict[str, object], *, summary_limit: int = 500) -> str:
    summary = compact_text(str(message.get("executor_summary", "")).strip(), limit=summary_limit)
    if summary:
        return summary

    status = compact_text(str(message.get("executor_status", "")).strip(), limit=24)
    analysis = compact_text(str(message.get("executor_analysis", "")).strip(), limit=160)
    confidence = float(message.get("executor_confidence", 0.0) or 0.0)
    recommended_action = compact_text(str(message.get("executor_recommended_action", "")).strip(), limit=36)
    missing = [
        str(item).strip()
        for item in message.get("executor_missing_params", [])
        if str(item).strip()
    ]

    parts: list[str] = []
    label = (
        f"[Executor|{status or 'unknown'}|{confidence:.2f}]"
        if confidence > 0
        else f"[Executor|{status or 'unknown'}]"
    )
    parts.append(label)
    if analysis:
        parts.append(f"分析: {analysis}")
    if recommended_action:
        parts.append(f"建议动作: {recommended_action}")
    if missing:
        parts.append(f"缺失参数: {', '.join(missing[:5])}")
    return "；".join(parts)


__all__ = ["build_executor_context", "compact_text"]
