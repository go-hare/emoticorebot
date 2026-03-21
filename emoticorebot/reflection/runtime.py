"""Reflection runtime around the reflection governor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.reflection.governor import ReflectionGovernor


class ReflectionRuntime:
    """Owns governed reflection services without background deep-reflection timers."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        workspace: Path,
        emotion_manager: EmotionStateManager | None = None,
        reflection_llm: Any | None = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ) -> None:
        self._governor = ReflectionGovernor(
            bus=bus,
            workspace=workspace,
            emotion_manager=emotion_manager,
            reflection_llm=reflection_llm,
            memory_config=memory_config,
            providers_config=providers_config,
        )

    def register(self) -> None:
        self._governor.register()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.close()

    def close(self) -> None:
        self._governor.close()

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15):
        return await self._governor.run_deep_reflection(reason=reason, warm_limit=warm_limit)

    async def rollback_anchor(self, **kwargs: Any):
        return await self._governor.rollback_anchor(**kwargs)

    @property
    def governor(self) -> ReflectionGovernor:
        return self._governor

    @property
    def persona(self):
        return self._governor.persona


__all__ = ["ReflectionRuntime"]
