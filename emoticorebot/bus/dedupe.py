"""Dedupe support for the priority pub/sub bus."""

from __future__ import annotations

from collections import OrderedDict


class DedupeCache:
    """Bounded in-memory cache keyed by `dedupe_key`."""

    def __init__(self, *, max_entries: int = 4096) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, None] = OrderedDict()

    def remember(self, dedupe_key: str | None) -> bool:
        if not dedupe_key:
            return True
        if dedupe_key in self._entries:
            return False
        self._entries[dedupe_key] = None
        self._entries.move_to_end(dedupe_key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return True


__all__ = ["DedupeCache"]
