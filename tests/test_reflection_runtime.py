from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType
from emoticorebot.reflection.runtime import ReflectionRuntime


async def _wait_for(predicate, *, timeout: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


async def _exercise_reflection_runtime_owns_periodic_deep_timer(workspace: Path) -> None:
    bus = PriorityPubSubBus()
    runtime = ReflectionRuntime(
        bus=bus,
        workspace=workspace,
        deep_interval_seconds=0.02,
        deep_warm_limit=9,
    )
    runtime.register()

    captured: list[BusEnvelope[ProtocolModel]] = []

    async def _capture(event: BusEnvelope[ReflectionSignalPayload]) -> None:
        captured.append(event)

    bus.subscribe(consumer="reflection_governor", event_type=EventType.REFLECTION_DEEP, handler=_capture)

    try:
        await runtime.start()
        await asyncio.sleep(0.05)
        await bus.drain()
        await _wait_for(lambda: bool(captured))
    finally:
        await runtime.stop()

    assert captured
    first = captured[0]
    assert first.source == "reflection"
    assert first.target == "reflection_governor"
    assert first.payload.reason == "periodic_signal"
    assert first.payload.metadata["trigger"] == "timer"
    assert first.payload.metadata["warm_limit"] == 9


def test_reflection_runtime_owns_periodic_deep_timer() -> None:
    with TemporaryDirectory() as tmp_dir:
        asyncio.run(_exercise_reflection_runtime_owns_periodic_deep_timer(Path(tmp_dir)))



