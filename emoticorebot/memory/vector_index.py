"""Vector index helpers for long-term memory retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig


@dataclass(frozen=True)
class VectorSearchHit:
    memory_id: str
    score: float


class EmbeddingFactory:
    """Create an in-process Chroma embedding function."""

    def __init__(self, *, providers_config: ProvidersConfig | None, memory_config: MemoryConfig | None):
        self.providers_config = providers_config or ProvidersConfig()
        self.memory_config = memory_config or MemoryConfig()

    def build(self) -> Any | None:
        try:
            from chromadb.utils import embedding_functions
        except Exception as exc:
            logger.warning("Chroma embedding functions are unavailable: {}", exc)
            return None

        vector_cfg = self.memory_config.vector
        provider = str(vector_cfg.embedding_provider or "default").strip().lower() or "default"
        model = str(vector_cfg.embedding_model or "").strip()

        if provider in {"", "default", "chroma"}:
            return embedding_functions.DefaultEmbeddingFunction()
        if provider in {"sentence_transformer", "local"}:
            return embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=model or "all-MiniLM-L6-v2",
                device="cpu",
            )

        logger.warning("Memory embedding provider not supported for Chroma: {}", provider)
        return None


class ChromaPersistentIndex:
    """A tiny Chroma PersistentClient wrapper used as the vector mirror of long-term memory."""

    _FAILED_PATHS: ClassVar[set[str]] = set()

    def __init__(
        self,
        *,
        workspace: Path,
        embedding_function: Any,
        collection_name: str = "long_term_memory",
    ):
        self.workspace = workspace
        self.embedding_function = embedding_function
        self.collection_name = collection_name
        self._client: Any | None = None

    @property
    def index_dir(self) -> Path:
        path = self.workspace / "memory" / "vector"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sync_state_path(self) -> Path:
        return self.index_dir / "_sync_state.json"

    @property
    def access_stats_path(self) -> Path:
        return self.index_dir / "_access_stats.json"

    def is_enabled(self) -> bool:
        return self.embedding_function is not None and self._get_client() is not None

    def close(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            close_method = getattr(client, "close", None)
            if callable(close_method):
                close_method()
        except Exception as exc:
            logger.warning("Chroma client close failed for {}: {}", self.index_dir, exc)
        finally:
            self._client = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def search(self, query: str, *, limit: int = 8, record_access: bool = True) -> list[VectorSearchHit]:
        client = self._get_client()
        if client is None or self.embedding_function is None:
            return []

        text = str(query or "").strip()
        if not text:
            return []

        try:
            collection = self._get_collection(client)
            result = collection.query(
                query_texts=[text],
                n_results=max(1, limit),
                include=["metadatas", "distances"],
            )
        except Exception as exc:
            logger.warning("Chroma vector search failed: {}", exc)
            return []

        ids = list((result or {}).get("ids", []) or [])
        distances = list((result or {}).get("distances", []) or [])
        metadatas = list((result or {}).get("metadatas", []) or [])
        row_ids = ids[0] if ids else []
        row_distances = distances[0] if distances else []
        row_metadatas = metadatas[0] if metadatas else []

        hits: list[VectorSearchHit] = []
        for memory_id, distance, metadata in zip(row_ids, row_distances, row_metadatas, strict=False):
            payload = dict(metadata or {}) if isinstance(metadata, dict) else {}
            resolved_id = str(payload.get("memory_id", memory_id) or "").strip()
            if not resolved_id:
                continue
            try:
                numeric_distance = float(distance or 0.0)
            except Exception:
                numeric_distance = 0.0
            score = 1.0 / (1.0 + max(0.0, numeric_distance))
            hits.append(VectorSearchHit(memory_id=resolved_id, score=score))
        if record_access and hits:
            self.record_accesses(hits)
        return hits

    def rebuild(self, records: list[dict[str, Any]], *, source_signature: dict[str, Any]) -> bool:
        client = self._get_client()
        if client is None or self.embedding_function is None:
            return False

        normalized_records = [record for record in records if str(record.get("memory_id", "") or "").strip()]
        try:
            self._reset_collection(client)
            collection = self._get_collection(client)
            if normalized_records:
                self._upsert_records(collection, normalized_records)
            self._prune_access_stats({str(record.get("memory_id", "") or "").strip() for record in normalized_records})
            self._write_sync_state(source_signature)
            return True
        except Exception as exc:
            logger.warning("Chroma rebuild failed: {}", exc)
            return False

    def upsert_many(self, records: list[dict[str, Any]], *, source_signature: dict[str, Any] | None = None) -> bool:
        client = self._get_client()
        if client is None or self.embedding_function is None:
            return False

        normalized_records = [record for record in records if str(record.get("memory_id", "") or "").strip()]
        if not normalized_records:
            if source_signature is not None:
                self._write_sync_state(source_signature)
            return False
        try:
            collection = self._get_collection(client)
            self._upsert_records(collection, normalized_records)
            if source_signature is not None:
                self._write_sync_state(source_signature)
            return True
        except Exception as exc:
            logger.warning("Chroma incremental upsert failed: {}", exc)
            return False

    def is_in_sync(self, source_signature: dict[str, Any]) -> bool:
        stored = self._read_sync_state()
        return bool(stored) and stored == dict(source_signature or {})

    def record_accesses(self, hits: list[VectorSearchHit]) -> None:
        if not hits:
            return
        current = self._read_access_stats()
        now = datetime.now().astimezone().isoformat()
        for hit in hits:
            memory_id = str(hit.memory_id or "").strip()
            if not memory_id:
                continue
            row = dict(current.get(memory_id, {}) or {})
            try:
                recall_count = int(row.get("recall_count", 0) or 0)
            except Exception:
                recall_count = 0
            row["recall_count"] = recall_count + 1
            row["last_retrieved_at"] = now
            row["last_relevance_score"] = round(float(hit.score or 0.0), 6)
            current[memory_id] = row
        self._write_access_stats(current)

    def _get_client(self) -> Any | None:
        path_key = str(self.index_dir)
        client = self._client
        if client is not None:
            return client
        if path_key in self._FAILED_PATHS:
            return None
        try:
            import chromadb

            client = chromadb.PersistentClient(path=path_key)
            self._client = client
            return client
        except Exception as exc:
            logger.warning("Chroma PersistentClient is unavailable for {}: {}", path_key, exc)
            self._FAILED_PATHS.add(path_key)
            return None

    def _get_collection(self, client: Any) -> Any:
        return client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"},
        )

    def _reset_collection(self, client: Any) -> None:
        try:
            client.delete_collection(name=self.collection_name)
        except Exception:
            pass

    def _upsert_records(self, collection: Any, records: list[dict[str, Any]]) -> None:
        ids = [str(record.get("memory_id", "") or "").strip() for record in records]
        documents = [self._record_text(record) for record in records]
        metadatas = [self._metadata(record) for record in records]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    @staticmethod
    def _metadata(record: dict[str, Any]) -> dict[str, str | int | float | bool]:
        tags = [str(item).strip() for item in list(record.get("tags", []) or []) if str(item).strip()]
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        return {
            "memory_id": str(record.get("memory_id", "") or "").strip(),
            "memory_type": str(record.get("memory_type", "") or "").strip(),
            "subtype": str(metadata.get("subtype", "") or "").strip(),
            "status": str(record.get("status", "") or "").strip(),
            "tags_text": " ".join(tags),
            "importance": int(metadata.get("importance", 5) or 5),
            "confidence": float(record.get("confidence", 0.0) or 0.0),
            "stability": float(record.get("stability", 0.0) or 0.0),
            "created_at": str(record.get("created_at", "") or "").strip(),
            "updated_at": str(record.get("updated_at", "") or "").strip(),
        }

    @staticmethod
    def _record_text(record: dict[str, Any]) -> str:
        tags = [str(item).strip() for item in list(record.get("tags", []) or []) if str(item).strip()]
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        parts = [
            str(record.get("summary", "") or "").strip(),
            str(record.get("detail", "") or "").strip(),
            " ".join(tags),
            str(metadata.get("hint", "") or "").strip(),
            str(metadata.get("trigger", "") or "").strip(),
        ]
        return "\n".join(part for part in parts if part)

    def _read_sync_state(self) -> dict[str, Any]:
        if not self.sync_state_path.exists():
            return {}
        try:
            payload = json.loads(self.sync_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _write_sync_state(self, payload: dict[str, Any]) -> None:
        self._write_json_file(self.sync_state_path, dict(payload or {}))

    def _read_access_stats(self) -> dict[str, Any]:
        if not self.access_stats_path.exists():
            return {}
        try:
            payload = json.loads(self.access_stats_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _write_access_stats(self, payload: dict[str, Any]) -> None:
        self._write_json_file(self.access_stats_path, dict(payload or {}))

    def _prune_access_stats(self, valid_ids: set[str]) -> None:
        current = self._read_access_stats()
        if not current:
            return
        normalized_ids = {str(item or "").strip() for item in valid_ids if str(item or "").strip()}
        pruned = {memory_id: row for memory_id, row in current.items() if memory_id in normalized_ids}
        if pruned == current:
            return
        self._write_access_stats(pruned)

    @staticmethod
    def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)


__all__ = ["EmbeddingFactory", "ChromaPersistentIndex", "VectorSearchHit"]
