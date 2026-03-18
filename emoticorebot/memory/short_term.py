"""Session-scoped short-term memory persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from emoticorebot.utils.helpers import ensure_dir, safe_filename
from emoticorebot.utils.llm_utils import normalize_content_blocks


def _new_memory_id() -> str:
    return f"stm_{uuid4().hex[:16]}"


def _normalize_raw_message(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    content_blocks = normalize_content_blocks(value.get("content_blocks", value.get("content", [])))
    content = value.get("content")
    if isinstance(content, str):
        text = content
    else:
        text_parts = [
            str(block.get("text", ""))
            for block in content_blocks
            if isinstance(block, dict) and str(block.get("type", "") or "").strip() == "text"
        ]
        text = "\n".join(part for part in text_parts if part)

    payload = {
        "role": str(value.get("role", "") or "").strip() or "user",
        "content": text,
        "content_blocks": content_blocks,
    }
    for key in ("session_id", "turn_id", "message_id", "job_id"):
        text_value = str(value.get(key, "") or "").strip()
        if text_value:
            payload[key] = text_value
    created_at = str(value.get("created_at", "") or value.get("timestamp", "") or "").strip()
    if created_at:
        payload["created_at"] = created_at
    return payload


def normalize_short_term_entry(entry: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    payload = dict(entry or {})
    created_at_text = str(payload.get("created_at", "") or now.isoformat()).strip()
    updated_at_text = str(payload.get("updated_at", "") or created_at_text).strip()
    ttl_seconds = _safe_int(payload.get("ttl_seconds"), default=24 * 3600)
    expires_at = str(payload.get("expires_at", "") or "").strip()
    if not expires_at and ttl_seconds > 0:
        try:
            expires_at = (datetime.fromisoformat(created_at_text) + timedelta(seconds=ttl_seconds)).isoformat()
        except Exception:
            expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

    raw_messages = [
        normalized
        for normalized in (_normalize_raw_message(item) for item in list(payload.get("raw_messages", []) or []))
        if normalized
    ]
    return {
        "memory_id": str(payload.get("memory_id", "") or _new_memory_id()).strip(),
        "session_id": str(payload.get("session_id", "") or "").strip(),
        "user_id": str(payload.get("user_id", "") or "").strip(),
        "turn_id": str(payload.get("turn_id", "") or "").strip(),
        "memory_type": str(payload.get("memory_type", "") or "turn_summary").strip() or "turn_summary",
        "summary": str(payload.get("summary", "") or "").strip(),
        "detail": str(payload.get("detail", "") or payload.get("summary", "") or "").strip(),
        "raw_messages": raw_messages,
        "source_event_ids": _normalize_str_list(payload.get("source_event_ids")),
        "ttl_seconds": ttl_seconds,
        "expires_at": expires_at,
        "created_at": created_at_text,
        "updated_at": updated_at_text,
        "metadata": dict(payload.get("metadata", {}) or {}),
    }


def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


@dataclass(frozen=True)
class ShortTermMemoryStore:
    workspace: Path

    @property
    def root(self) -> Path:
        return ensure_dir(self.workspace / "memory" / "short_term")

    @staticmethod
    def safe_session_id(session_id: str) -> str:
        return safe_filename(str(session_id or "").replace(":", "_"))

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{self.safe_session_id(session_id)}.jsonl"

    def append_entries(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        path = self.path_for(session_id)
        with path.open("a", encoding="utf-8") as file_obj:
            for entry in entries:
                normalized = normalize_short_term_entry({**entry, "session_id": session_id})
                file_obj.write(json.dumps(normalized, ensure_ascii=False) + "\n")

    def load_entries(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file_obj:
            for raw_line in file_obj:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    entries.append(normalize_short_term_entry(payload))
        if limit is not None:
            return entries[-limit:]
        return entries

    def clear(self, session_id: str) -> None:
        path = self.path_for(session_id)
        if path.exists():
            path.write_text("", encoding="utf-8")


__all__ = ["ShortTermMemoryStore", "normalize_short_term_entry"]
