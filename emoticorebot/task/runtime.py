"""Task runtime that owns runtime dispatch and worker execution."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.execution.team import AgentTeam
from emoticorebot.protocol.commands import RightBrainJobRequestPayload, TaskCancelPayload, TaskCreatePayload, TaskResumePayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import RightBrainAcceptedPayload, RightBrainRejectedPayload, RightBrainResultPayload, TaskEndPayload
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
        self._bus.subscribe(consumer="runtime", event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED, handler=self._dispatch_right_job)
        self._bus.subscribe(consumer="runtime", event_type=EventType.RUNTIME_ARCHIVE_TASK, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.SYSTEM_TIMEOUT, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_REPLIED, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.OUTPUT_DELIVERY_FAILED, handler=self._dispatch)
        self._bus.subscribe(consumer="runtime", event_type=EventType.TASK_END, handler=self._relay_right_result)
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

    async def _dispatch_right_job(self, event: BusEnvelope[RightBrainJobRequestPayload]) -> None:
        try:
            command_event = self._map_right_job_to_task_command(event)
        except Exception as exc:
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.RIGHT_EVENT_JOB_REJECTED,
                    source="right_runtime",
                    target="broadcast",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    task_id=event.task_id,
                    correlation_id=event.correlation_id or event.payload.job_id,
                    causation_id=event.event_id,
                    payload=RightBrainRejectedPayload(
                        job_id=event.payload.job_id,
                        reason=str(exc),
                        metadata={"job_action": event.payload.job_action},
                    ),
                )
            )
            return
        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_EVENT_JOB_ACCEPTED,
                source="right_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.payload.job_id,
                causation_id=event.event_id,
                payload=RightBrainAcceptedPayload(
                    job_id=event.payload.job_id,
                    stage="dispatch",
                    reason="job accepted by right runtime",
                    metadata={
                        "job_action": event.payload.job_action,
                        "right_brain_strategy": event.payload.right_brain_strategy,
                    },
                ),
            )
        )
        await self._dispatch(command_event)

    async def _relay_right_result(self, event: BusEnvelope[TaskEndPayload]) -> None:
        summary = str(event.payload.summary or "").strip() or None
        result_text = str(event.payload.output or event.payload.error or "").strip() or None
        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_EVENT_RESULT_READY,
                source="right_runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.task_id,
                causation_id=event.event_id,
                payload=RightBrainResultPayload(
                    job_id=f"job_result_{event.payload.task_id}",
                    job_action="create_task",
                    task_id=event.payload.task_id,
                    summary=summary,
                    result_text=result_text,
                    metadata={"result": event.payload.result},
                ),
            )
        )

    @staticmethod
    def _new_command_id() -> str:
        return f"cmd_{uuid4().hex[:12]}"

    def _map_right_job_to_task_command(self, event: BusEnvelope[RightBrainJobRequestPayload]) -> BusEnvelope[ProtocolModel]:
        payload = event.payload
        task_id = str(payload.task_id or "").strip()
        correlation_id = task_id or event.correlation_id or event.turn_id or payload.job_id
        if payload.job_action == "create_task":
            request = str(payload.request_text or payload.source_text or "").strip()
            if not request:
                raise RuntimeError("missing request_text for create_task")
            return cast(
                BusEnvelope[ProtocolModel],
                build_envelope(
                    event_type=EventType.TASK_CREATE,
                    source="right_runtime",
                    target="runtime",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    task_id=task_id or None,
                    correlation_id=correlation_id,
                    causation_id=event.event_id,
                    payload=TaskCreatePayload(
                        command_id=self._new_command_id(),
                        request=request,
                        goal=payload.goal,
                        context=dict(payload.context or {}),
                        message=str(payload.metadata.get("task_reason", "") or "").strip() or None,
                    ),
                ),
            )
        if payload.job_action == "resume_task":
            if not task_id:
                raise RuntimeError("missing task_id for resume_task")
            return cast(
                BusEnvelope[ProtocolModel],
                build_envelope(
                    event_type=EventType.TASK_RESUME,
                    source="right_runtime",
                    target="runtime",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    task_id=task_id,
                    correlation_id=correlation_id,
                    causation_id=event.event_id,
                    payload=TaskResumePayload(
                        command_id=self._new_command_id(),
                        task_id=task_id,
                        state="running",
                        user_input=str(payload.request_text or payload.source_text or "").strip() or None,
                        context=dict(payload.context or {}),
                        message=str(payload.metadata.get("task_reason", "") or "").strip() or "user_follow_up",
                    ),
                ),
            )
        if not task_id:
            raise RuntimeError("missing task_id for cancel_task")
        return cast(
            BusEnvelope[ProtocolModel],
            build_envelope(
                event_type=EventType.TASK_CANCEL,
                source="right_runtime",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=task_id,
                correlation_id=correlation_id,
                causation_id=event.event_id,
                payload=TaskCancelPayload(
                    command_id=self._new_command_id(),
                    task_id=task_id,
                    reason=str(payload.context.get("reason", "") or payload.request_text or "").strip() or None,
                    by="user",
                ),
            ),
        )

    def _guard_task_event(self, event: BusEnvelope[ProtocolModel]) -> BusEnvelope[ProtocolModel]:
        if event.event_type != EventType.TASK_END:
            return event
        guarded = self._guard.guard_task_event(cast(BusEnvelope[TaskEndPayload], event))
        return cast(BusEnvelope[ProtocolModel], guarded)


__all__ = ["TaskRuntime"]
