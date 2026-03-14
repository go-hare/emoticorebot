"""Background reflection entrypoint backed by deep reflection service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from emoticorebot.bootstrap import RuntimeHost


@dataclass(frozen=True)
class ReflectionResult:
    summary: str = ""
    memory_ids: list[str] = field(default_factory=list)
    memory_count: int = 0
    skill_hint_count: int = 0
    materialized_skills: list[str] = field(default_factory=list)
    materialized_skill_count: int = 0


class ReflectionEngine:
    """Periodic deep reflection that consolidates long-term memory."""

    def __init__(self, runtime: "RuntimeHost", workspace: Path):
        self.runtime = runtime
        self.workspace = workspace

    async def run_cycle(self, warm_limit: int = 15) -> ReflectionResult:
        result = await self.runtime.run_deep_reflection(reason="periodic_signal", warm_limit=warm_limit)

        if result.memory_count:
            logger.info("ReflectionEngine: consolidated {} long-term memories", result.memory_count)
        elif warm_limit:
            logger.debug("ReflectionEngine: nothing to consolidate")
        if result.materialized_skill_count:
            logger.info(
                "ReflectionEngine: materialized {} skills ({})",
                result.materialized_skill_count,
                ", ".join(result.materialized_skills[:5]),
            )

        return ReflectionResult(
            summary=result.summary,
            memory_ids=list(result.memory_ids),
            memory_count=int(result.memory_count),
            skill_hint_count=int(result.skill_hint_count),
            materialized_skills=list(result.materialized_skills),
            materialized_skill_count=int(result.materialized_skill_count),
        )


__all__ = ["ReflectionEngine", "ReflectionResult"]
