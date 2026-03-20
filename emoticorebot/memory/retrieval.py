"""Read-side memory retrieval helpers for the main-brain layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig

from .store import MemoryStore


class MemoryRetrieval:
    """Main-brain-facing facade over the unified memory store."""

    def __init__(
        self,
        workspace: Path,
        *,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ) -> None:
        self._store = MemoryStore(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )

    def query_main_brain_memories(self, *, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return self._store.query(query, memory_types=("relationship", "fact", "working", "reflection"), limit=limit)

    def build_task_memory_bundle(self, *, query: str, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
        return self._store.build_task_bundle(query=query, limit=limit)

    def build_main_brain_context(self, *, query: str, limit: int = 8) -> str:
        return self._store.build_main_brain_context(query=query, limit=limit)

    def close(self) -> None:
        self._store.close()


__all__ = ["MemoryRetrieval"]

