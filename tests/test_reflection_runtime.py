from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.reflection.runtime import ReflectionRuntime


async def _exercise_reflection_runtime_start_is_passive(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    runtime = ReflectionRuntime(
        bus=bus,
        workspace=workspace,
    )
    runtime.register()

    captured: list[BusEnvelope[ReflectionSignalPayload]] = []

    async def _capture(event: BusEnvelope[ReflectionSignalPayload]) -> None:
        captured.append(event)

    bus.subscribe(consumer="reflection_governor", event_type=EventType.REFLECTION_LIGHT, handler=_capture)

    await runtime.start()
    await asyncio.sleep(0.05)
    await bus.drain()
    await runtime.stop()

    assert captured == []


def test_reflection_runtime_start_is_passive() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_reflection_runtime_start_is_passive(Path(tmp_dir)))
