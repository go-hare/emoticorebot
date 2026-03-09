"""Background reflection entrypoint backed by deep reflection service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.services.deep_reflection import DeepReflectionService

if TYPE_CHECKING:
    from emoticorebot.runtime.runtime import FusionRuntime


@dataclass(frozen=True)
class ReflectionResult:
    persona_delta: str | None = None
    user_insight: str | None = None
    memory_updates: list[str] = field(default_factory=list)
    insight_count: int = 0


class ReflectionEngine:
    """Periodic deep reflection that consolidates long-term memory."""

    def __init__(self, runtime: "FusionRuntime", workspace: Path):
        self.runtime = runtime
        self.workspace = workspace
        self.service = DeepReflectionService(workspace, runtime.iq_llm)

    async def run_cycle(self, warm_limit: int = 15) -> ReflectionResult:
        recent_events = CognitiveEvent.retrieve(self.workspace, query="", k=max(6, warm_limit))
        if not recent_events:
            logger.debug("ReflectionEngine: no event memories, skip")
            return ReflectionResult()

        result = await self.service.run_cycle(recent_events)
        if result.insight_count:
            logger.info("ReflectionEngine: consolidated {} long-term memories", result.insight_count)
        return ReflectionResult(
            persona_delta=result.persona_delta,
            user_insight=result.user_insight,
            memory_updates=list(result.memory_updates),
            insight_count=result.insight_count,
        )


__all__ = ["ReflectionEngine", "ReflectionResult"]
