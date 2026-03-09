"""Background reflection entrypoint backed by deep reflection service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.services.deep_reflection import DeepReflectionService
from emoticorebot.services.tool_deep_reflection import ToolDeepReflectionService

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
        self.service = DeepReflectionService(workspace, runtime.main_brain_llm)
        self.tool_service = ToolDeepReflectionService(workspace, runtime.main_brain_llm)

    async def run_cycle(self, warm_limit: int = 15) -> ReflectionResult:
        recent_events = CognitiveEvent.retrieve(self.workspace, query="", k=max(6, warm_limit))
        persona_delta: str | None = None
        user_insight: str | None = None
        memory_updates: list[str] = []
        insight_count = 0

        if recent_events:
            result = await self.service.run_cycle(recent_events)
            persona_delta = result.persona_delta
            user_insight = result.user_insight
            memory_updates.extend(result.memory_updates)
            insight_count += result.insight_count
        else:
            logger.debug("ReflectionEngine: no event memories for deep reflection")

        tool_result = await self.tool_service.run_cycle(limit=max(12, warm_limit * 2))
        memory_updates.extend(tool_result.memory_updates)
        insight_count += tool_result.insight_count

        if insight_count:
            logger.info("ReflectionEngine: consolidated {} long-term memories", insight_count)
        elif not recent_events:
            logger.debug("ReflectionEngine: no tool reflections, skip")

        return ReflectionResult(
            persona_delta=persona_delta,
            user_insight=user_insight,
            memory_updates=memory_updates,
            insight_count=insight_count,
        )


__all__ = ["ReflectionEngine", "ReflectionResult"]
