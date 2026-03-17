"""Process-local memory service facade introduced by the refactor skeleton."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.memory.store import MemoryStore


class ProcessMemoryService:
    """Thin wrapper over the current memory store for incremental migration."""

    def __init__(
        self,
        *,
        workspace: Path,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ) -> None:
        self._store = MemoryStore(workspace, memory_config=memory_config, providers_config=providers_config)

    def recent(self, *, limit: int = 10):
        return self._store.recent(limit=limit)

    def close(self) -> None:
        self._store.close()

    @property
    def store(self) -> MemoryStore:
        return self._store


__all__ = ["ProcessMemoryService"]
