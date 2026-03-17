"""Small facade for process-local memory access during the refactor transition."""

from __future__ import annotations

from emoticorebot.memory.service import ProcessMemoryService


class MemoryFacade:
    """Read-oriented facade used by upper layers during migration."""

    def __init__(self, service: ProcessMemoryService) -> None:
        self._service = service

    def recent(self, *, limit: int = 10):
        return self._service.recent(limit=limit)


__all__ = ["MemoryFacade"]
