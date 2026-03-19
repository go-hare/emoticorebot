"""Reflection runtime around the reflection governor."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.reflection.governor import ReflectionGovernor


class ReflectionRuntime:
    """Owns reflection orchestration and governed memory updates."""

    _SYSTEM_SESSION_ID = "system:memory"

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        workspace: Path,
        emotion_manager: EmotionStateManager | None = None,
        reflection_llm: Any | None = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
        deep_interval_seconds: float = 7200.0,
        deep_warm_limit: int = 15,
    ) -> None:
        self._bus = bus
        self._governor = ReflectionGovernor(
            bus=bus,
            workspace=workspace,
            emotion_manager=emotion_manager,
            reflection_llm=reflection_llm,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self._deep_interval_seconds = max(float(deep_interval_seconds), 0.0)
        self._deep_warm_limit = max(int(deep_warm_limit), 1)
        self._timer_task: asyncio.Task[None] | None = None

    def register(self) -> None:
        self._governor.register()

    async def start(self) -> None:
        if self._deep_interval_seconds <= 0:
            return
        if self._timer_task is not None and not self._timer_task.done():
            return
        self._timer_task = asyncio.create_task(self._run_timer_loop(), name="reflection:deep-timer")

    async def stop(self) -> None:
        task = self._timer_task
        self._timer_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.close()

    def close(self) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()
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

    async def _run_timer_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._deep_interval_seconds)
                await self._publish_periodic_deep_reflection_signal()
        except asyncio.CancelledError:
            raise

    async def _publish_periodic_deep_reflection_signal(self) -> None:
        scheduled_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        await self._bus.publish(
            build_envelope(
                event_type=EventType.REFLECTION_DEEP,
                source="reflection",
                target="reflection_governor",
                session_id=self._SYSTEM_SESSION_ID,
                turn_id="turn_background_reflection",
                correlation_id="background_reflection",
                payload=ReflectionSignalPayload(
                    trigger_id=f"reflection_timer_{uuid4().hex[:12]}",
                    reason="periodic_signal",
                    recent_context_ids=[],
                    metadata={
                        "trigger": "timer",
                        "scheduled_at": scheduled_at,
                        "warm_limit": self._deep_warm_limit,
                    },
                ),
            )
        )


__all__ = ["ReflectionRuntime"]





