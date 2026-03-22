"""Vector mirror for long-term memory retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.state.io import ensure_directory, read_json, write_json


class EmbeddingFactory:
    """Create an in-process Chroma embedding function."""

    def __init__(self, providers_config: ProvidersConfig | None, memory_config: MemoryConfig | None):
        self.providers_config = providers_config or ProvidersConfig()
        self.memory_config = memory_config or MemoryConfig()

    def build(self) -> Any | None:
        try:
            from chromadb.utils import embedding_functions
        except Exception as exc:
            logger.warning("Chroma embedding is unavailable: {}", exc)
            return None

        vector_config = self.memory_config.vector
        provider = str(vector_config.embedding_provider or "default").strip().lower() or "default"
        model_name = str(vector_config.embedding_model or "").strip()

        if provider in {"", "default", "chroma"}:
            return embedding_functions.DefaultEmbeddingFunction()
        if provider in {"sentence_transformer", "local"}:
            return embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=model_name or "all-MiniLM-L6-v2",
                device="cpu",
            )

        logger.warning("Unsupported embedding provider: {}", provider)
        return None


class VectorMirror:
    """Persistent vector mirror of flattened long-term memory candidates."""

    def __init__(self, workspace: Path, providers_config: ProvidersConfig | None, memory_config: MemoryConfig | None):
        self.workspace = workspace
        self.memory_config = memory_config or MemoryConfig()
        self.embedding_function = EmbeddingFactory(providers_config, memory_config).build()
        self.client: Any | None = None

    @property
    def index_dir(self) -> Path:
        return ensure_directory(self.workspace / "memory" / "vector")

    @property
    def sync_path(self) -> Path:
        return self.index_dir / "sync.json"

    def is_enabled(self) -> bool:
        return self.embedding_function is not None and self.get_client() is not None

    def get_client(self) -> Any | None:
        if self.client is not None:
            return self.client
        if self.embedding_function is None:
            return None
        try:
            import chromadb

            self.client = chromadb.PersistentClient(path=str(self.index_dir))
            return self.client
        except Exception as exc:
            logger.warning("Failed to open Chroma client: {}", exc)
            return None

    def get_collection(self) -> Any:
        client = self.get_client()
        if client is None:
            raise RuntimeError("Vector mirror is not available")
        return client.get_or_create_collection(
            name="long_term_memory",
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"},
        )

    def rebuild(self, candidates: list[dict[str, Any]], signature: dict[str, Any]) -> None:
        if not self.is_enabled():
            return
        unique_candidates = self.deduplicate_candidates(candidates)
        collection = self.get_collection()
        try:
            self.get_client().delete_collection(name="long_term_memory")
        except Exception:
            pass
        collection = self.get_collection()
        if unique_candidates:
            collection.upsert(
                ids=[item["memory_id"] for item in unique_candidates],
                documents=[self.build_document(item) for item in unique_candidates],
                metadatas=[self.build_metadata(item) for item in unique_candidates],
            )
        write_json(self.sync_path, signature)

    def upsert(self, candidates: list[dict[str, Any]], signature: dict[str, Any]) -> None:
        if not self.is_enabled() or not candidates:
            return
        unique_candidates = self.deduplicate_candidates(candidates)
        if not unique_candidates:
            write_json(self.sync_path, signature)
            return
        collection = self.get_collection()
        collection.upsert(
            ids=[item["memory_id"] for item in unique_candidates],
            documents=[self.build_document(item) for item in unique_candidates],
            metadatas=[self.build_metadata(item) for item in unique_candidates],
        )
        write_json(self.sync_path, signature)

    def is_in_sync(self, signature: dict[str, Any]) -> bool:
        return read_json(self.sync_path) == dict(signature)

    def search(self, query: str, limit: int) -> dict[str, float]:
        if not self.is_enabled():
            return {}
        text = str(query or "").strip()
        if not text:
            return {}
        result = self.get_collection().query(
            query_texts=[text],
            n_results=max(1, limit),
            include=["metadatas", "distances"],
        )
        ids = list((result or {}).get("ids", []) or [])
        distances = list((result or {}).get("distances", []) or [])
        metadatas = list((result or {}).get("metadatas", []) or [])
        row_ids = ids[0] if ids else []
        row_distances = distances[0] if distances else []
        row_metadatas = metadatas[0] if metadatas else []
        scores: dict[str, float] = {}
        for memory_id, distance, metadata in zip(row_ids, row_distances, row_metadatas, strict=False):
            resolved_id = str((metadata or {}).get("memory_id", memory_id) or "").strip()
            if not resolved_id:
                continue
            numeric_distance = float(distance or 0.0)
            scores[resolved_id] = 1.0 / (1.0 + max(0.0, numeric_distance))
        return scores

    def build_document(self, candidate: dict[str, Any]) -> str:
        parts = [
            str(candidate.get("summary", "") or "").strip(),
            str(candidate.get("detail", "") or "").strip(),
            " ".join(str(item).strip() for item in list(candidate.get("tags", []) or []) if str(item).strip()),
        ]
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        hint = str(metadata.get("hint", "") or "").strip()
        if hint:
            parts.append(hint)
        return "\n".join(part for part in parts if part)

    def build_metadata(self, candidate: dict[str, Any]) -> dict[str, Any]:
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        return {
            "memory_id": str(candidate.get("memory_id", "") or "").strip(),
            "memory_type": str(candidate.get("memory_type", "") or "").strip(),
            "subtype": str(metadata.get("subtype", "") or "").strip(),
            "confidence": float(candidate.get("confidence", 0.0) or 0.0),
            "stability": float(candidate.get("stability", 0.0) or 0.0),
        }

    def deduplicate_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique_by_id: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates, start=1):
            memory_id = str(candidate.get("memory_id", "") or "").strip() or f"memory_candidate_{index}"
            normalized = dict(candidate)
            normalized["memory_id"] = memory_id
            unique_by_id[memory_id] = normalized
        return list(unique_by_id.values())
