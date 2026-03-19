"""Helpers for formal long-term memory candidates produced by reflection."""

from __future__ import annotations

import re
from typing import Any


FORMAL_MEMORY_TYPES = {"relationship", "fact", "working", "execution", "reflection"}


def normalize_memory_candidates(
    value: Any,
    *,
    default_memory_type: str,
    default_subtype: str = "",
    default_confidence: float = 0.8,
    default_stability: float = 0.5,
    default_importance: int | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        normalized = normalize_memory_candidate(
            item,
            default_memory_type=default_memory_type,
            default_subtype=default_subtype,
            default_confidence=default_confidence,
            default_stability=default_stability,
            default_importance=default_importance,
        )
        if normalized:
            records.append(normalized)
    return records[: max(1, limit)]


def normalize_memory_candidate(
    item: Any,
    *,
    default_memory_type: str,
    default_subtype: str = "",
    default_confidence: float = 0.8,
    default_stability: float = 0.5,
    default_importance: int | None = None,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    summary = str(item.get("summary", "") or "").strip()
    detail = str(item.get("detail", "") or "").strip()
    if not summary and not detail:
        return {}

    metadata = dict(item.get("metadata", {}) or {}) if isinstance(item.get("metadata"), dict) else {}
    subtype = str(metadata.get("subtype", "") or default_subtype).strip()
    if subtype:
        metadata["subtype"] = subtype

    if "importance" not in metadata:
        if default_importance is not None:
            metadata["importance"] = _clamp_int(default_importance, default=5, minimum=1, maximum=10)

    return {
        "memory_type": _normalize_memory_type(item.get("memory_type"), default=default_memory_type),
        "summary": summary or compact_text(detail, limit=120),
        "detail": detail or summary,
        "confidence": _clamp_float(item.get("confidence"), default=default_confidence, minimum=0.0, maximum=1.0),
        "stability": _clamp_float(item.get("stability"), default=default_stability, minimum=0.0, maximum=1.0),
        "tags": _normalize_str_list(item.get("tags")),
        "metadata": metadata,
    }


def build_skill_hint_candidate(
    *,
    summary: str,
    detail: str,
    trigger: str,
    hint: str,
    skill_name: str,
    confidence: float = 0.8,
    stability: float = 0.85,
    importance: int = 7,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    normalized_name = _normalize_skill_name(skill_name or summary or hint or trigger or detail)
    if not normalized_name:
        return {}
    return {
        "memory_type": "execution",
        "summary": summary.strip() or compact_text(detail or hint, limit=120),
        "detail": detail.strip() or hint.strip() or summary.strip(),
        "confidence": _clamp_float(confidence, default=0.8, minimum=0.0, maximum=1.0),
        "stability": _clamp_float(stability, default=0.85, minimum=0.0, maximum=1.0),
        "tags": _normalize_str_list(tags or ["skill", "hint"]),
        "metadata": {
            "subtype": "skill_hint",
            "importance": _clamp_int(importance, default=7, minimum=1, maximum=10),
            "skill_id": f"skill_{re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '_', normalized_name.lower()).strip('_') or 'hint'}",
            "skill_name": normalized_name,
            "trigger": str(trigger or "").strip(),
            "hint": str(hint or detail or summary or "").strip(),
            "applies_to_tools": [],
        },
    }


def compact_text(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _normalize_memory_type(value: Any, *, default: str) -> str:
    text = str(value or default).strip()
    return text if text in FORMAL_MEMORY_TYPES else default


def _normalize_skill_name(value: str) -> str:
    compact = re.sub(r"\s+", "-", str(value or "").strip().lower())
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", compact).strip("-")
    normalized = re.sub(r"-+", "-", normalized)
    return normalized[:64]


def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items[:8]


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except Exception:
        numeric = default
    return max(minimum, min(maximum, numeric))


def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        numeric = float(value)
    except Exception:
        numeric = default
    return max(minimum, min(maximum, numeric))


__all__ = [
    "FORMAL_MEMORY_TYPES",
    "build_skill_hint_candidate",
    "compact_text",
    "normalize_memory_candidate",
    "normalize_memory_candidates",
]
