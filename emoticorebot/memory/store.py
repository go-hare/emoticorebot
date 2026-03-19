"""Append-only long-term memory store."""

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
from emoticorebot.utils.llm_utils import normalize_content_blocks


FORMAL_MEMORY_TYPES = {"relationship", "fact", "working", "execution", "reflection"}
BRAIN_MEMORY_TYPES = {"relationship", "fact", "working", "reflection"}
TASK_MEMORY_TYPES = {"working", "execution", "reflection"}
SKILL_HINT_SUBTYPES = {"skill_hint", "skill"}
TASK_EXPERIENCE_SUBTYPES = {"workflow", "tool_experience", "error_pattern", "workflow_pattern"}


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
    def long_term_dir(self) -> Path:
        path = self.memory_dir / "long_term"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def path(self) -> Path:
        return self.long_term_dir / "memory.jsonl"

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

    def close(self) -> None:
        vector_index = self._vector_index
        if vector_index is not None:
            try:
                vector_index.close()
            except Exception:
                pass
        object.__setattr__(self, "_vector_index", None)
        object.__setattr__(self, "_vector_ready", False)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

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
                appended_ids.append(str(normalized["memory_id"]))
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
        memory_types: Sequence[str] | None = None,
        subtypes: Sequence[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        all_records = self.read_all()
        records = self._filter_records(
            all_records,
            memory_types=memory_types,
            subtypes=subtypes,
        )
        if not records:
            return []

        query_text = str(query or "").strip()
        lexical_scores = {
            str(record.get("memory_id", "") or ""): self._score(record, query=query_text)
            for record in records
        }
        vector_index = self._get_vector_index()
        vector_scores = self._vector_scores(
            query_text,
            all_records=all_records,
            allowed_ids={str(record.get("memory_id", "") or "") for record in records},
            limit=limit,
            vector_index=vector_index,
        )

        if not vector_scores:
            scored = sorted(
                records,
                key=lambda record: lexical_scores.get(str(record.get("memory_id", "") or ""), 0.0),
                reverse=True,
            )
        else:
            keyword_weight, vector_weight = self._hybrid_weights()
            scored = sorted(
                records,
                key=lambda record: self._hybrid_score(
                    lexical=lexical_scores.get(str(record.get("memory_id", "") or ""), 0.0),
                    vector=vector_scores.get(str(record.get("memory_id", "") or "")),
                    keyword_weight=keyword_weight,
                    vector_weight=vector_weight,
                ),
                reverse=True,
            )

        selected = scored if limit <= 0 else scored[:limit]
        self._record_vector_accesses(records=selected, vector_scores=vector_scores, vector_index=vector_index)
        return selected

    def build_left_brain_context(self, *, query: str, limit: int = 8) -> str:
        records = self.query(query, memory_types=tuple(BRAIN_MEMORY_TYPES), limit=limit)
        if not records:
            return ""

        lines: list[str] = []
        for record in records:
            record_type = str(record.get("memory_type", "memory") or "memory")
            summary = self._compact(str(record.get("summary", "") or record.get("detail", "")), limit=120)
            if not summary:
                continue
            importance = self._record_importance(record)
            confidence = float(record.get("confidence", 0.0) or 0.0)
            lines.append(f"- [{record_type}|imp={importance}|conf={confidence:.2f}] {summary}")

        if not lines:
            return ""
        return "## 长期记忆\n" + "\n".join(lines)

    def build_task_bundle(self, *, query: str, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
        experience = self.query(
            query,
            memory_types=tuple(TASK_MEMORY_TYPES),
            subtypes=tuple(TASK_EXPERIENCE_SUBTYPES),
            limit=max(2, limit),
        )
        general = self.query(
            query,
            memory_types=("execution", "working", "reflection"),
            limit=max(2, limit),
        )
        hints = self.query(
            query,
            memory_types=("execution", "working"),
            subtypes=tuple(SKILL_HINT_SUBTYPES),
            limit=3,
        )

        return {
            "relevant_task_memories": [self._compact_record(record) for record in general[:3]],
            "relevant_tool_memories": [self._compact_record(record) for record in experience[:3]],
            "skill_hints": [self._compact_skill_hint_record(record) for record in hints[:3]],
        }

    def recent(self, *, limit: int = 10) -> list[dict[str, Any]]:
        records = self.read_all()
        return records[-limit:] if limit > 0 else records

    @staticmethod
    def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        payload = dict(record or {})
        metadata = MemoryStore._metadata_dict(payload)
        summary = str(payload.get("summary", "") or "").strip()
        detail = str(payload.get("detail", "") or "").strip()
        if not summary:
            summary = MemoryStore._compact(detail, limit=120)
        if not detail:
            detail = summary

        memory_id = str(payload.get("memory_id", "") or f"mem_{uuid4().hex}").strip()
        session_id = str(payload.get("session_id", "") or "").strip()
        user_id = str(payload.get("user_id", "") or metadata.get("user_id", "") or "").strip()
        source_module = str(
            payload.get("source_module", "")
            or payload.get("source_component", "")
            or ""
        ).strip()

        normalized = {
            "schema_version": "memory.long_term.v1",
            "memory_id": memory_id,
            "user_id": user_id,
            "session_id": session_id,
            "memory_type": MemoryStore._normalize_memory_type(payload, metadata=metadata),
            "summary": summary,
            "detail": detail,
            "evidence_messages": MemoryStore._normalize_evidence_messages(payload),
            "source_module": source_module,
            "source_event_ids": MemoryStore._normalize_source_event_ids(payload),
            "confidence": MemoryStore._clamp_float(payload.get("confidence"), default=0.8, minimum=0.0, maximum=1.0),
            "stability": MemoryStore._clamp_float(payload.get("stability"), default=0.5, minimum=0.0, maximum=1.0),
            "tags": MemoryStore._normalize_str_list(payload.get("tags")),
            "status": MemoryStore._normalize_enum(
                payload.get("status"),
                default="active",
                allowed={"active", "superseded", "invalid"},
            ),
            "created_at": str(payload.get("created_at", "") or now),
            "updated_at": str(payload.get("updated_at", "") or payload.get("created_at", "") or now),
            "metadata": metadata,
        }
        return normalized

    @staticmethod
    def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        compact = {
            "memory_id": str(record.get("memory_id", "") or ""),
            "memory_type": str(record.get("memory_type", "") or ""),
            "summary": MemoryStore._compact(str(record.get("summary", "") or ""), limit=120),
            "detail": MemoryStore._compact(str(record.get("detail", "") or ""), limit=180),
            "confidence": float(record.get("confidence", 0.0) or 0.0),
        }
        subtype = str(metadata.get("subtype", "") or "").strip()
        if subtype:
            compact["subtype"] = subtype
        return compact

    @staticmethod
    def _compact_skill_hint_record(record: dict[str, Any]) -> dict[str, Any]:
        compact = MemoryStore._compact_record(record)
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        hint_payload = {
            key: value
            for key, value in {
                "skill_name": str(metadata.get("skill_name", "") or "").strip(),
                "skill_id": str(metadata.get("skill_id", "") or "").strip(),
                "trigger": str(metadata.get("trigger", "") or "").strip(),
                "hint": str(metadata.get("hint", "") or "").strip(),
            }.items()
            if value
        }
        if hint_payload:
            compact["metadata"] = hint_payload
        return compact

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
            memory_id = str(record.get("memory_id", "") or "").strip()
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
        memory_types: Sequence[str] | None,
        subtypes: Sequence[str] | None,
    ) -> list[dict[str, Any]]:
        filtered = list(records)
        if memory_types:
            allowed = {str(item).strip() for item in memory_types if str(item).strip()}
            filtered = [record for record in filtered if str(record.get("memory_type", "")) in allowed]
        if subtypes:
            allowed = {str(item).strip() for item in subtypes if str(item).strip()}
            filtered = [record for record in filtered if self._record_subtype(record) in allowed]
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
        memory_type = str(record.get("memory_type", "") or "").strip()
        summary = str(record.get("summary", "") or "").strip()
        detail = str(record.get("detail", "") or "").strip()
        if not memory_type or not detail:
            return None
        return (memory_type, summary, detail)

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
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        text = " ".join(
            [
                str(record.get("summary", "") or ""),
                str(record.get("detail", "") or ""),
                " ".join(self._normalize_str_list(record.get("tags"))),
                " ".join(self._normalize_str_list(metadata.get("keywords"))),
            ]
        )
        query_tokens = self._tokenize(query)
        text_tokens = self._tokenize(text)

        overlap = 0.0
        if query_tokens and text_tokens:
            overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))

        importance = float(self._record_importance(record)) / 10.0
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

    @staticmethod
    def _metadata_dict(payload: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(payload.get("metadata", {}) or {}) if isinstance(payload.get("metadata"), dict) else {}
        return metadata

    @staticmethod
    def _normalize_memory_type(payload: dict[str, Any], *, metadata: dict[str, Any]) -> str:
        memory_type = str(payload.get("memory_type", "") or "").strip()
        if memory_type in FORMAL_MEMORY_TYPES:
            return memory_type

        request_type = str(payload.get("memory_type", "") or "").strip()
        if request_type == "persona":
            metadata.setdefault("subtype", "persona")
            return "reflection"
        if request_type == "user_model":
            metadata.setdefault("subtype", "user_model")
            return "relationship"
        if request_type == "tool_experience":
            metadata.setdefault("subtype", "tool_experience")
            return "execution"
        if request_type == "task_experience":
            metadata.setdefault("subtype", "workflow")
            return "execution"
        if request_type == "episodic":
            metadata.setdefault("subtype", "turn_insight")
            return "reflection"
        return "fact"

    @staticmethod
    def _normalize_source_event_ids(payload: dict[str, Any]) -> list[str]:
        direct = payload.get("source_event_ids")
        if isinstance(direct, list):
            return MemoryStore._normalize_str_list(direct)
        return []

    @staticmethod
    def _normalize_evidence_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        direct = payload.get("evidence_messages")
        if isinstance(direct, list):
            normalized = [MemoryStore._normalize_evidence_message(item) for item in direct]
            return [item for item in normalized if item]
        return []

    @staticmethod
    def _normalize_evidence_message(value: Any) -> dict[str, Any]:
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

        normalized: dict[str, Any] = {
            "role": str(value.get("role", "") or "").strip() or "user",
            "content": text,
            "content_blocks": content_blocks,
        }
        for key in ("session_id", "turn_id", "message_id", "job_id"):
            text_value = str(value.get(key, "") or "").strip()
            if text_value:
                normalized[key] = text_value
        created_at = str(value.get("created_at", "") or value.get("timestamp", "") or "").strip()
        if created_at:
            normalized["created_at"] = created_at
        return normalized

    @staticmethod
    def _record_subtype(record: dict[str, Any]) -> str:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return str(metadata.get("subtype", "") or "").strip()

    @staticmethod
    def _record_importance(record: dict[str, Any]) -> int:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return MemoryStore._clamp_int(metadata.get("importance"), default=5, minimum=1, maximum=10)


__all__ = ["MemoryStore"]

