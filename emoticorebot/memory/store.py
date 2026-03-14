"""Unified `memory.jsonl` store for long-term memory."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from uuid import uuid4

from loguru import logger

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.memory.vector_index import ChromaPersistentIndex, EmbeddingFactory, VectorSearchHit


BRAIN_AUDIENCES = {"brain", "shared"}
TASK_AUDIENCES = {"task", "shared"}

TASK_EXPERIENCE_TYPES = {
    "turn_insight",
    "tool_experience",
    "error_pattern",
    "workflow_pattern",
}
SKILL_HINT_TYPES = {"skill_hint", "skill"}


@dataclass(frozen=True)
class MemoryStore:
    """Append-only source of truth for long-term memory."""

    workspace: Path
    memory_config: MemoryConfig | None = None
    providers_config: ProvidersConfig | None = None
    _vector_index: Any | None = field(default=None, init=False, repr=False, compare=False)
    _vector_ready: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def memory_dir(self) -> Path:
        path = self.workspace / "memory"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def path(self) -> Path:
        return self.memory_dir / "memory.jsonl"

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as file_obj:
            for raw_line in file_obj:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
        return records

    def append_many(self, records: Iterable[dict[str, Any]]) -> list[str]:
        existing_keys = {
            self._dedupe_key(record)
            for record in self.read_all()
            if self._dedupe_key(record) is not None
        }
        appended_ids: list[str] = []
        appended_records: list[dict[str, Any]] = []
        with self.path.open("a", encoding="utf-8") as file_obj:
            for record in records:
                normalized = self.normalize_record(record)
                dedupe_key = self._dedupe_key(normalized)
                if dedupe_key is not None and dedupe_key in existing_keys:
                    continue
                file_obj.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                appended_ids.append(str(normalized["id"]))
                appended_records.append(normalized)
                if dedupe_key is not None:
                    existing_keys.add(dedupe_key)

        vector_index = self._get_vector_index()
        if appended_records and vector_index is not None and vector_index.is_enabled():
            vector_index.upsert_many(appended_records, source_signature=self._source_signature())
        return appended_ids

    def query(
        self,
        query: str = "",
        *,
        audiences: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        types: Sequence[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        all_records = self.read_all()
        records = self._filter_records(
            all_records,
            audiences=audiences,
            kinds=kinds,
            types=types,
        )
        if not records:
            return []

        query_text = str(query or "").strip()
        lexical_scores = {
            str(record.get("id", "") or ""): self._score(record, query=query_text)
            for record in records
        }
        vector_index = self._get_vector_index()
        vector_scores = self._vector_scores(
            query_text,
            all_records=all_records,
            allowed_ids={str(record.get("id", "") or "") for record in records},
            limit=limit,
            vector_index=vector_index,
        )

        if not vector_scores:
            scored = sorted(
                records,
                key=lambda record: lexical_scores.get(str(record.get("id", "") or ""), 0.0),
                reverse=True,
            )
        else:
            keyword_weight, vector_weight = self._hybrid_weights()
            scored = sorted(
                records,
                key=lambda record: self._hybrid_score(
                    lexical=lexical_scores.get(str(record.get("id", "") or ""), 0.0),
                    vector=vector_scores.get(str(record.get("id", "") or "")),
                    keyword_weight=keyword_weight,
                    vector_weight=vector_weight,
                ),
                reverse=True,
            )

        selected = scored if limit <= 0 else scored[:limit]
        self._record_vector_accesses(
            records=selected,
            vector_scores=vector_scores,
            vector_index=vector_index,
        )
        return selected

    def build_brain_context(self, *, query: str, limit: int = 8) -> str:
        records = self.query(query, audiences=tuple(BRAIN_AUDIENCES), limit=limit)
        if not records:
            return ""

        lines: list[str] = []
        for record in records:
            record_type = str(record.get("type", "memory") or "memory")
            summary = self._compact(str(record.get("summary", "") or record.get("content", "")), limit=120)
            if not summary:
                continue
            importance = int(record.get("importance", 5) or 5)
            confidence = float(record.get("confidence", 0.0) or 0.0)
            lines.append(f"- [{record_type}|imp={importance}|conf={confidence:.2f}] {summary}")

        if not lines:
            return ""
        return "## 长期记忆\n" + "\n".join(lines)

    def build_task_bundle(self, *, query: str, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
        experience = self.query(
            query,
            audiences=tuple(TASK_AUDIENCES),
            types=tuple(TASK_EXPERIENCE_TYPES),
            limit=max(2, limit),
        )
        hints = self.query(
            query,
            audiences=tuple(TASK_AUDIENCES),
            types=tuple(SKILL_HINT_TYPES),
            limit=3,
        )

        return {
            "relevant_task_memories": [self._compact_record(record) for record in experience[:3]],
            "relevant_tool_memories": [
                self._compact_record(record)
                for record in experience
                if str(record.get("type", "")) in {"tool_experience", "error_pattern", "workflow_pattern"}
            ][:3],
            "skill_hints": [self._compact_record(record) for record in hints[:3]],
        }

    def recent(self, *, limit: int = 10) -> list[dict[str, Any]]:
        records = self.read_all()
        return records[-limit:] if limit > 0 else records

    @staticmethod
    def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        payload = dict(record or {})
        summary = str(payload.get("summary", "") or "").strip()
        content = str(payload.get("content", "") or "").strip()
        if not summary:
            summary = MemoryStore._compact(content, limit=120)
        if not content:
            content = summary

        normalized = {
            "schema_version": "memory.v1",
            "id": str(payload.get("id", "") or f"mem_{uuid4().hex}"),
            "created_at": str(payload.get("created_at", "") or now),
            "audience": MemoryStore._normalize_audience(payload.get("audience")),
            "kind": MemoryStore._normalize_enum(payload.get("kind"), default="episodic", allowed={"episodic", "durable", "procedural"}),
            "type": str(payload.get("type", "turn_insight") or "turn_insight").strip(),
            "summary": summary,
            "content": content,
            "importance": MemoryStore._clamp_int(payload.get("importance"), default=5, minimum=1, maximum=10),
            "confidence": MemoryStore._clamp_float(payload.get("confidence"), default=0.8, minimum=0.0, maximum=1.0),
            "stability": MemoryStore._clamp_float(payload.get("stability"), default=0.5, minimum=0.0, maximum=1.0),
            "status": MemoryStore._normalize_enum(payload.get("status"), default="active", allowed={"active", "superseded", "invalid", "expired"}),
            "tags": MemoryStore._normalize_str_list(payload.get("tags")),
            "source": dict(payload.get("source", {}) or {}),
            "links": dict(payload.get("links", {}) or {}),
            "payload": dict(payload.get("payload", {}) or {}),
            "expires_at": payload.get("expires_at"),
            "metadata": dict(payload.get("metadata", {}) or {}),
        }
        return normalized

    @staticmethod
    def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(record.get("id", "") or ""),
            "type": str(record.get("type", "") or ""),
            "summary": MemoryStore._compact(str(record.get("summary", "") or ""), limit=120),
            "content": MemoryStore._compact(str(record.get("content", "") or ""), limit=180),
            "importance": int(record.get("importance", 5) or 5),
            "confidence": float(record.get("confidence", 0.0) or 0.0),
        }

    def _get_vector_index(self) -> ChromaPersistentIndex | None:
        if self._vector_ready:
            return self._vector_index

        index: ChromaPersistentIndex | None = None
        memory_config = self.memory_config or MemoryConfig()
        vector_cfg = memory_config.vector
        backend = str(vector_cfg.backend or "").strip().lower()
        if backend == "chroma":
            embedding_function = EmbeddingFactory(
                providers_config=self.providers_config,
                memory_config=memory_config,
            ).build()
            if embedding_function is not None:
                index = ChromaPersistentIndex(
                    workspace=self.workspace,
                    embedding_function=embedding_function,
                )
            else:
                logger.warning("Memory vector retrieval is configured but Chroma embedding function could not be initialized")

        object.__setattr__(self, "_vector_index", index)
        object.__setattr__(self, "_vector_ready", True)
        return index

    def _vector_scores(
        self,
        query: str,
        *,
        all_records: list[dict[str, Any]],
        allowed_ids: set[str],
        limit: int,
        vector_index: ChromaPersistentIndex | None = None,
    ) -> dict[str, float]:
        if not str(query or "").strip() or not allowed_ids:
            return {}

        vector_index = vector_index or self._get_vector_index()
        if vector_index is None or not vector_index.is_enabled():
            return {}

        self._sync_vector_index(all_records, vector_index=vector_index)
        search_limit = max(limit * 4, self._vector_top_k(), 12)
        hits = vector_index.search(query, limit=search_limit, record_access=False)
        scores: dict[str, float] = {}
        for hit in hits:
            if hit.memory_id not in allowed_ids:
                continue
            current = scores.get(hit.memory_id, 0.0)
            if hit.score > current:
                scores[hit.memory_id] = hit.score
        return scores

    def _record_vector_accesses(
        self,
        *,
        records: list[dict[str, Any]],
        vector_scores: dict[str, float],
        vector_index: ChromaPersistentIndex | None,
    ) -> None:
        if not records or not vector_scores or vector_index is None or not vector_index.is_enabled():
            return
        hits: list[VectorSearchHit] = []
        seen: set[str] = set()
        for record in records:
            memory_id = str(record.get("id", "") or "").strip()
            if not memory_id or memory_id in seen or memory_id not in vector_scores:
                continue
            hits.append(VectorSearchHit(memory_id=memory_id, score=vector_scores[memory_id]))
            seen.add(memory_id)
        if hits:
            vector_index.record_accesses(hits)

    def _sync_vector_index(self, records: list[dict[str, Any]], *, vector_index: ChromaPersistentIndex | None = None) -> None:
        index = vector_index or self._get_vector_index()
        if index is None or not index.is_enabled():
            return
        signature = self._source_signature()
        if index.is_in_sync(signature):
            return
        index.rebuild(records, source_signature=signature)

    def _source_signature(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"exists": False}
        stat = self.path.stat()
        return {
            "exists": True,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    def _filter_records(
        self,
        records: list[dict[str, Any]],
        *,
        audiences: Sequence[str] | None,
        kinds: Sequence[str] | None,
        types: Sequence[str] | None,
    ) -> list[dict[str, Any]]:
        filtered = list(records)
        if audiences:
            allowed = {str(item).strip() for item in audiences if str(item).strip()}
            filtered = [record for record in filtered if str(record.get("audience", "")) in allowed]
        if kinds:
            allowed = {str(item).strip() for item in kinds if str(item).strip()}
            filtered = [record for record in filtered if str(record.get("kind", "")) in allowed]
        if types:
            allowed = {str(item).strip() for item in types if str(item).strip()}
            filtered = [record for record in filtered if str(record.get("type", "")) in allowed]
        return filtered

    def _hybrid_weights(self) -> tuple[float, float]:
        memory_config = self.memory_config or MemoryConfig()
        keyword_weight = self._clamp_float(memory_config.vector.keyword_weight, default=0.45, minimum=0.0, maximum=1.0)
        vector_weight = self._clamp_float(memory_config.vector.vector_weight, default=0.55, minimum=0.0, maximum=1.0)
        total = keyword_weight + vector_weight
        if total <= 1e-6:
            return 0.45, 0.55
        return keyword_weight / total, vector_weight / total

    def _vector_top_k(self) -> int:
        memory_config = self.memory_config or MemoryConfig()
        return self._clamp_int(memory_config.vector.top_k, default=24, minimum=1, maximum=200)

    @staticmethod
    def _hybrid_score(
        *,
        lexical: float,
        vector: float | None,
        keyword_weight: float,
        vector_weight: float,
    ) -> float:
        if vector is None:
            return lexical
        return (lexical * keyword_weight) + (max(0.0, vector) * vector_weight)

    @staticmethod
    def _normalize_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _normalize_enum(value: Any, *, default: str, allowed: set[str]) -> str:
        text = str(value or default).strip()
        return text if text in allowed else default

    @staticmethod
    def _normalize_audience(value: Any) -> str:
        text = str(value or "shared").strip()
        return text if text in {"brain", "task", "shared"} else "shared"

    @staticmethod
    def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            numeric = int(value)
        except Exception:
            numeric = default
        return max(minimum, min(maximum, numeric))

    @staticmethod
    def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
        try:
            numeric = float(value)
        except Exception:
            numeric = default
        return max(minimum, min(maximum, numeric))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if token}

    @staticmethod
    def _compact(text: str, *, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _dedupe_key(record: dict[str, Any]) -> tuple[str, str, str] | None:
        record_type = str(record.get("type", "") or "").strip()
        summary = str(record.get("summary", "") or "").strip()
        content = str(record.get("content", "") or "").strip()
        if not record_type or not content:
            return None
        return (record_type, summary, content)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None

    def _score(self, record: dict[str, Any], *, query: str) -> float:
        text = " ".join(
            [
                str(record.get("summary", "") or ""),
                str(record.get("content", "") or ""),
                " ".join(self._normalize_str_list(record.get("tags"))),
            ]
        )
        query_tokens = self._tokenize(query)
        text_tokens = self._tokenize(text)

        overlap = 0.0
        if query_tokens and text_tokens:
            overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))

        importance = float(self._clamp_int(record.get("importance", 5), default=5, minimum=1, maximum=10)) / 10.0
        confidence = self._clamp_float(record.get("confidence", 0.0), default=0.0, minimum=0.0, maximum=1.0)
        stability = self._clamp_float(record.get("stability", 0.0), default=0.0, minimum=0.0, maximum=1.0)

        recency = 0.0
        created_at = self._parse_datetime(record.get("created_at"))
        if created_at is not None:
            age_hours = max(0.0, (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() / 3600.0)
            recency = math.exp(-age_hours / 168.0)

        if not query_tokens:
            overlap = 0.25

        return (overlap * 0.45) + (importance * 0.25) + (confidence * 0.15) + (stability * 0.10) + (recency * 0.05)
