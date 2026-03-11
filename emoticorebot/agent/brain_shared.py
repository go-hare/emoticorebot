"""Shared helpers for the brain layer."""

from __future__ import annotations

import json
import re
from typing import Any


def compact_text(text: Any, limit: int = 160) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."


def parse_json_dict(text: str) -> dict[str, Any] | None:
    raw = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def extract_json_string_field(raw: str, field: str) -> str:
    pattern = rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, raw, flags=re.DOTALL)
    if not match:
        return ""
    value = match.group(1)
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.replace("\\n", "\n").replace('\\"', '"').strip()


def extract_json_bool_field(raw: str, field: str) -> bool | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', raw, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


__all__ = [
    "compact_text",
    "parse_json_dict",
    "extract_json_string_field",
    "extract_json_bool_field",
]
