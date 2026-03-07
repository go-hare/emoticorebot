"""Registry for lightweight IQ experts."""

from __future__ import annotations

from emoticorebot.experts.base import BaseExpert


class ExpertRegistry:
    def __init__(self) -> None:
        self._experts: dict[str, BaseExpert] = {}

    def register(self, expert: BaseExpert) -> None:
        self._experts[expert.name] = expert

    def get(self, name: str) -> BaseExpert | None:
        return self._experts.get(name)

    def names(self) -> list[str]:
        return list(self._experts.keys())


__all__ = ["ExpertRegistry"]
