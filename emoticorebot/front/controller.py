"""Front runtime around the executive brain implementation."""

from __future__ import annotations

from typing import Any

from emoticorebot.brain.executive import ExecutiveBrain
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.right.store import RightBrainStore


class FrontRuntime:
    """Owns front-instance execution through the executive brain."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        task_store: RightBrainStore,
        brain_llm: Any | None = None,
        context_builder: Any | None = None,
        session_runtime: Any | None = None,
    ) -> None:
        self._brain = ExecutiveBrain(
            bus=bus,
            task_store=task_store,
            brain_llm=brain_llm,
            context_builder=context_builder,
            session_runtime=session_runtime,
        )

    def register(self) -> None:
        self._brain.register()

    async def stop(self) -> None:
        await self._brain.stop()

    @property
    def brain(self) -> ExecutiveBrain:
        return self._brain


__all__ = ["FrontRuntime"]
