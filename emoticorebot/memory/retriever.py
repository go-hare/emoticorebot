from __future__ import annotations

from typing import Any

from emoticorebot.memory.memory_facade import MemoryFacade


class MemoryRetriever:
    _EQ_SECTION_LIMIT = 480

    def __init__(self, memory: MemoryFacade):
        self.memory = memory

    def build_iq_sections(self, query: str = "") -> list[str]:
        sections: list[str] = []
        for section in (
            self.memory.semantic.get_context(query=query),
            self.memory.episodic.get_context(query=query, k=4),
            self.memory.plans.get_context(k=5),
            self.memory.reflective.get_context(query=query, k=2),
            self.memory.events.get_context(query=query, k=5),
        ):
            if section:
                sections.append(section)
        return sections

    def build_eq_sections(
        self,
        *,
        query: str = "",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
    ) -> list[str]:
        sections: list[str] = []
        warm = self.memory.relational.get_context(query=query, current_emotion=current_emotion)
        if warm:
            sections.append(self._compact_section(warm, self._EQ_SECTION_LIMIT))

        if pad_state:
            affective = self.memory.affective.get_context(*pad_state, query=query)
            if affective:
                sections.append(self._compact_section(affective, self._EQ_SECTION_LIMIT))

        for section in (
            self.memory.reflective.get_context(query=query, k=2),
            self.memory.episodic.get_context(query=query, k=1),
        ):
            if section:
                sections.append(self._compact_section(section, self._EQ_SECTION_LIMIT))
        return sections[:4]

    @staticmethod
    def _compact_section(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"


__all__ = ["MemoryRetriever"]
