"""Pure memory IO service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.memory import MemoryStore


class MemoryService:
    """Only handles memory persistence and retrieval."""

    def __init__(
        self,
        workspace: Path,
        *,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ):
        self.workspace = workspace
        self.store = MemoryStore(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )

    def append_many(self, records: Iterable[dict[str, Any]]) -> list[str]:
        return self.store.append_many(list(records or []))

    def query(
        self,
        query: str,
        *,
        audiences: tuple[str, ...] = ("brain", "shared"),
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        return self.store.query(query, audiences=audiences, limit=limit)

    def build_task_bundle(self, *, query: str, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
        return self.store.build_task_bundle(query=query, limit=limit)

    def build_brain_context(self, *, query: str, limit: int = 8) -> str:
        return self.store.build_brain_context(query=query, limit=limit)


__all__ = ["MemoryService"]
