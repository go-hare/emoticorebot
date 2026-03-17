"""Runtime bus coordinator that applies scheduler decisions to the shared bus."""

from __future__ import annotations

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType, Topic

from .scheduler import RuntimeScheduler


class RuntimeService:
    """Subscribes runtime-owned topics and republishes normalized scheduler outputs."""

    def __init__(self, *, bus: PriorityPubSubBus, scheduler: RuntimeScheduler | None = None) -> None:
        self._bus = bus
        self._scheduler = scheduler or RuntimeScheduler()

    @property
    def scheduler(self) -> RuntimeScheduler:
        return self._scheduler

    def register(self) -> None:
        self._bus.subscribe(consumer="runtime", topic=Topic.BRAIN_COMMAND, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", topic=Topic.TASK_COMMAND, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", topic=Topic.TASK_REPORT, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.RUNTIME_ARCHIVE_TASK, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.SYSTEM_TIMEOUT, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_REPLIED, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_DELIVERY_FAILED, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", topic=Topic.SAFETY_EVENT, handler=self._ignore)

    async def _dispatch(self, event: BusEnvelope[ProtocolModel]) -> None:
        for emitted in self._scheduler.dispatch(event):
            await self._bus.publish(emitted)

    @staticmethod
    async def _ignore(_event: BusEnvelope[ProtocolModel]) -> None:
        return None


__all__ = ["RuntimeService"]
