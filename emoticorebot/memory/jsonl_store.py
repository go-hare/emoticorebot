from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def resolve_eq_memory_file(workspace: Path, filename: str) -> Path:
    new_path = workspace / "memory" / "eq" / filename
    legacy_path = workspace / "data" / "memory" / filename

    new_path.parent.mkdir(parents=True, exist_ok=True)
    if not new_path.exists() and legacy_path.exists():
        try:
            legacy_path.replace(new_path)
        except OSError:
            shutil.copy2(legacy_path, new_path)
    return new_path


class JsonlStore:
    def __init__(self, path: Path):
        self._file = path
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: dict[str, Any]) -> None:
        with self._file.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self._file.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self._file.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
        return entries

    def count(self) -> int:
        if not self._file.exists():
            return 0
        return sum(1 for _ in self._file.open("r", encoding="utf-8"))

    @staticmethod
    def _normalize_text(value: str, *, limit: int = 240) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _token_overlap(query: str, values: Iterable[str]) -> float:
        query_tokens = set(query.lower().split())
        if not query_tokens:
            return 0.5
        haystack = " ".join(values).lower()
        hay_tokens = set(haystack.split())
        if not hay_tokens:
            return 0.0
        return min(len(query_tokens & hay_tokens) / max(len(query_tokens), 1), 1.0)

    @staticmethod
    def _recency_score(timestamp: str) -> float:
        if not timestamp:
            return 0.3
        try:
            age_hours = (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds() / 3600
        except Exception:
            return 0.3
        return max(0.05, min(1.0, 0.995 ** max(age_hours, 0)))

    def _rank_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        query: str,
        text_fields: tuple[str, ...],
        limit: int,
        timestamp_field: str = "timestamp",
        importance_field: str = "importance",
        confidence_field: str = "confidence",
    ) -> list[dict[str, Any]]:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for entry in entries:
            values = [str(entry.get(field, "")) for field in text_fields if entry.get(field)]
            relevance = self._token_overlap(query, values)
            recency = self._recency_score(str(entry.get(timestamp_field, "") or ""))
            importance = float(entry.get(importance_field, 0.5) or 0.5)
            confidence = float(entry.get(confidence_field, 0.7) or 0.7)
            score = 0.4 * relevance + 0.25 * recency + 0.2 * importance + 0.15 * confidence
            ranked.append((score, entry))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in ranked[:limit]]


__all__ = ["JsonlStore", "resolve_eq_memory_file"]
