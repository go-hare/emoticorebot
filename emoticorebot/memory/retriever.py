from __future__ import annotations

from typing import Any

from emoticorebot.memory.memory_facade import MemoryFacade


class MemoryRetriever:
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
            sections.append(warm)

        if pad_state:
            affective = self.memory.affective.get_context(*pad_state, query=query)
            if affective:
                sections.append(affective)

        for section in (
            self.memory.reflective.get_context(query=query, k=4),
            self.memory.episodic.get_context(query=query, k=3),
        ):
            if section:
                sections.append(section)
        return sections


__all__ = ["MemoryRetriever"]
