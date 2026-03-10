"""Background reflection entrypoint backed by deep reflection service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from emoticorebot.runtime.runtime import EmoticoreRuntime


@dataclass(frozen=True)
class ReflectionResult:
    persona_delta: str | None = None
    user_insight: str | None = None
    memory_updates: list[str] = field(default_factory=list)
    insight_count: int = 0


class ReflectionEngine:
    """Periodic deep reflection that consolidates long-term memory."""

    def __init__(self, runtime: "EmoticoreRuntime", workspace: Path):
        self.runtime = runtime
        self.workspace = workspace

    async def run_cycle(self, warm_limit: int = 15) -> ReflectionResult:
        persona_delta: str | None = None
        user_insight: str | None = None
        memory_updates: list[str] = []
        insight_count = 0

        result = await self.runtime.run_deep_insight(reason="periodic_signal", warm_limit=warm_limit)
        if result.insight_count:
            persona_delta = result.persona_delta
            user_insight = result.user_insight
            memory_updates.extend(result.memory_updates)
            insight_count += result.insight_count

        if insight_count:
            logger.info("ReflectionEngine: consolidated {} long-term memories", insight_count)
        elif warm_limit:
            logger.debug("ReflectionEngine: nothing to consolidate")

        return ReflectionResult(
            persona_delta=persona_delta,
            user_insight=user_insight,
            memory_updates=memory_updates,
            insight_count=insight_count,
        )


__all__ = ["ReflectionEngine", "ReflectionResult"]
