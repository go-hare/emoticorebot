"""Task runtime that owns runtime dispatch and worker execution."""

from __future__ import annotations

from typing import Any, cast

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.execution.team import AgentTeam
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import TaskEndPayload
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType, Topic
from emoticorebot.safety.guard import SafetyGuard

from .coordinator import RuntimeScheduler


class TaskRuntime:
    """Coordinates runtime scheduling and agent execution for task actors."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        worker_llm: Any | None = None,
        context_builder: Any | None = None,
        tool_registry: Any | None = None,
    ) -> None:
        self._bus = bus
        self._scheduler = RuntimeScheduler()
        self._guard = SafetyGuard()
        self._team = AgentTeam(
            bus=bus,
            task_store=self._scheduler.task_store,
            worker_llm=worker_llm,
            context_builder=context_builder,
            tool_registry=tool_registry,
        )

    def register(self) -> None:
        self._bus.subscribe(consumer="runtime", topic=Topic.TASK_COMMAND, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", topic=Topic.TASK_REPORT, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.RUNTIME_ARCHIVE_TASK, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.SYSTEM_TIMEOUT, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_REPLIED, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_DELIVERY_FAILED, handler=self._dispatch)
        self._team.register()

    async def stop(self) -> None:
        await self._team.stop()

    @property
    def task_store(self):
        return self._scheduler.task_store

    @property
    def scheduler(self):
        return self._scheduler

    @property
    def team(self) -> AgentTeam:
        return self._team

    @property
    def worker(self):
        return self._team._worker

    async def _dispatch(self, event: BusEnvelope[ProtocolModel]) -> None:
        for emitted in self._scheduler.dispatch(event):
            await self._bus.publish(self._guard_task_event(emitted))

    def _guard_task_event(self, event: BusEnvelope[ProtocolModel]) -> BusEnvelope[ProtocolModel]:
        if event.event_type != EventType.TASK_END:
            return event
        guarded = self._guard.guard_task_event(cast(BusEnvelope[TaskEndPayload], event))
        return cast(BusEnvelope[ProtocolModel], guarded)


__all__ = ["TaskRuntime"]
