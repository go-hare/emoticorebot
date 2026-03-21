"""Process-local session runtime that materializes session/task view and world-model state."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import (
    BrainReplyRequestPayload,
    ExecutorJobRequestPayload,
    ExecutorResultContextPayload,
)
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    BrainReplyReadyPayload,
    OutputReadyPayloadBase,
    ExecutorRejectedPayload,
    ExecutorResultPayload,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    TurnInputPayload,
)
from emoticorebot.protocol.topics import EventType
from emoticorebot.executor.store import ExecutorStore
from emoticorebot.world_model import WorldModel, WorldModelStore

from .models import SessionContext, SessionTaskView
from .stream_runtime import SessionStreamRuntime
from .task_runtime import SessionTaskRuntime
from .world_model_runtime import SessionWorldModelRuntime


class SessionRuntime:
    """Maintains process-local session context and compact task views."""

    def __init__(self, *, bus: PriorityPubSubBus, task_store: ExecutorStore, world_model_store: WorldModelStore) -> None:
        self._bus = bus
        self._stream_state = SessionStreamRuntime()
        self._task_state = SessionTaskRuntime(task_store=task_store)
        self._world_state = SessionWorldModelRuntime(task_store=task_store, world_model_store=world_model_store)
        self._sessions: dict[str, SessionContext] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    def register(self) -> None:
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_TURN_RECEIVED, handler=self._on_user_input)
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STREAM_STARTED, handler=self._on_input_stream_started)
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STREAM_CHUNK, handler=self._on_input_stream_chunk)
        self._bus.subscribe(
            consumer="session",
            event_type=EventType.INPUT_STREAM_COMMITTED,
            handler=self._on_input_stream_committed,
        )
        self._bus.subscribe(
            consumer="session",
            event_type=EventType.INPUT_STREAM_INTERRUPTED,
            handler=self._on_input_stream_interrupted,
        )
        self._bus.subscribe(consumer="session", event_type=EventType.BRAIN_EVENT_REPLY_READY, handler=self._on_brain_reply_ready)
        self._bus.subscribe(
            consumer="session",
            event_type=EventType.EXECUTOR_EVENT_JOB_REJECTED,
            handler=self._on_executor_result_event,
        )
        self._bus.subscribe(
            consumer="session",
            event_type=EventType.EXECUTOR_EVENT_RESULT_READY,
            handler=self._on_executor_result_event,
        )
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_INLINE_READY, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_PUSH_READY, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_STREAM_OPEN, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_STREAM_DELTA, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=self._on_reply_output)

    def snapshot(self, session_id: str) -> SessionContext:
        context = self._get_or_create(session_id)
        return deepcopy(context)

    def task_views(self, session_id: str) -> list[SessionTaskView]:
        context = self._get_or_create(session_id)
        return self._task_state.task_views(context)

    def task_view(self, session_id: str, task_id: str) -> SessionTaskView | None:
        context = self._get_or_create(session_id)
        return self._task_state.task_view(context, task_id)

    def current_task_id(self, session_id: str) -> str:
        model = self._world_state.snapshot(session_id)
        if model.current_task is None:
            return ""
        return str(model.current_task.task_id or "").strip()

    def clear_session(self, session_id: str) -> None:
        session_key = str(session_id or "").strip()
        self._sessions.pop(session_key, None)
        self._session_locks.pop(session_key, None)
        self._world_state.clear_session(session_key)

    def should_archive(self, session_id: str) -> bool:
        context = self._get_or_create(session_id)
        return self._stream_state.should_archive(context) and self.current_task_id(session_id) == ""

    def update_memory_snapshot(self, session_id: str, snapshot: Mapping[str, Any] | None) -> None:
        context = self._get_or_create(session_id)
        context.memory_snapshot = dict(snapshot or {}) if snapshot is not None else None

    def world_model_snapshot(self, session_id: str) -> WorldModel:
        return self._world_state.snapshot(session_id)

    async def _on_user_input(self, event: BusEnvelope[TurnInputPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        brain_request: BusEnvelope[BrainReplyRequestPayload] | None = None
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            user_text = self._stream_state.apply_user_turn(context=context, event=event)
            self._world_state.on_user_input(session_id=session_id, user_text=user_text)
            context.archived = self.should_archive(session_id)
            brain_request = self._build_brain_reply_request(event)
        if brain_request is not None:
            await self._bus.publish(brain_request)

    async def _on_input_stream_started(self, event: BusEnvelope[StreamStartPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            self._stream_state.apply_input_stream_started(context=context, event=event)
            context.archived = self.should_archive(session_id)

    async def _on_input_stream_chunk(self, event: BusEnvelope[StreamChunkPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            self._stream_state.apply_input_stream_chunk(context=context, event=event)
            context.archived = self.should_archive(session_id)

    async def _on_input_stream_committed(self, event: BusEnvelope[StreamCommitPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        turn_event: BusEnvelope[TurnInputPayload] | None = None
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            committed_turn = self._stream_state.build_committed_turn(context=context, event=event)
            if committed_turn is None:
                context.archived = self.should_archive(session_id)
                return
            turn_id, turn_payload = committed_turn
            context.archived = self.should_archive(session_id)
            turn_event = build_envelope(
                event_type=EventType.INPUT_TURN_RECEIVED,
                source="session",
                target="broadcast",
                session_id=event.session_id,
                turn_id=turn_id,
                correlation_id=turn_id,
                causation_id=event.event_id,
                payload=turn_payload,
            )
        if turn_event is not None:
            await self._bus.publish(turn_event)

    async def _on_input_stream_interrupted(self, event: BusEnvelope[StreamInterruptedPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            self._stream_state.apply_input_stream_interrupted(context=context, event=event)
            context.archived = self.should_archive(session_id)

    async def _on_brain_reply_ready(self, event: BusEnvelope[BrainReplyReadyPayload]) -> None:
        requests = self._prepare_executor_requests(
            requests=self._brain_executor_requests(event.payload),
        )
        session_key = str(event.session_id or "").strip()
        if session_key:
            async with self._session_lock(session_key):
                previous_task_id = self.current_task_id(session_key)
                self._world_state.apply_brain_focus_requests(
                    session_id=session_key,
                    requests=requests,
                )
                requests = self._with_auto_cancel_for_replacement(
                    requests=requests,
                    previous_task_id=previous_task_id,
                )
                if self._should_finalize_reply_task(event, requests=requests):
                    self._world_state.finalize_completed_task(
                        session_id=session_key,
                        task_id=event.payload.related_task_id or event.task_id,
                    )
                context = self._get_or_create(session_key)
                context.archived = self.should_archive(session_key)
        if not event.payload.invoke_executor or not requests:
            return
        await self._dispatch_executor_requests(
            session_id=event.session_id,
            turn_id=event.turn_id,
            causation_id=event.event_id,
            correlation_id=event.correlation_id,
            related_task_id=event.payload.related_task_id or event.task_id,
            reply_request_id=event.payload.request_id,
            reply_kind=event.payload.reply_kind,
            requests=requests,
        )

    @staticmethod
    def _brain_executor_requests(payload: BrainReplyReadyPayload) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        for item in list(getattr(payload, "executor_requests", []) or []):
            if isinstance(item, Mapping):
                requests.append(dict(item))
        return requests

    async def _dispatch_executor_requests(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        causation_id: str | None,
        correlation_id: str | None,
        related_task_id: str | None,
        reply_request_id: str | None,
        reply_kind: str,
        requests: list[dict[str, Any]],
    ) -> None:
        for request in requests:
            job_action = str(request.get("job_action", "") or "").strip()
            if job_action not in {"execute", "cancel"}:
                continue
            job_id = str(request.get("job_id", "") or "").strip() or f"job_{uuid4().hex[:12]}"
            task_id = str(request.get("task_id", "") or "").strip() or None
            if job_action == "cancel" and task_id is None:
                task_id = str(related_task_id or "").strip() or None
            session_key = str(session_id or "").strip()
            if job_action == "execute" and job_id and session_key:
                self._world_state.record_job_blueprint(
                    session_id=session_key,
                    job_id=job_id,
                    request=request,
                )
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.EXECUTOR_COMMAND_JOB_REQUESTED,
                    source="session",
                    target="executor_runtime",
                    session_id=session_id,
                    turn_id=turn_id,
                    task_id=task_id,
                    correlation_id=task_id or correlation_id or turn_id,
                    causation_id=causation_id,
                    payload=ExecutorJobRequestPayload(
                        job_id=job_id,
                        job_action=job_action,
                        job_kind=str(request.get("job_kind", "") or "").strip() or None,
                        source_text=str(request.get("source_text", "") or "").strip() or None,
                        request_text=str(request.get("request_text", "") or "").strip() or None,
                        task_id=task_id,
                        goal=str(request.get("goal", "") or "").strip() or None,
                        mainline=list(request.get("mainline", []) or []),
                        current_stage=request.get("current_stage"),
                        current_checks=[str(item).strip() for item in list(request.get("current_checks", []) or []) if str(item).strip()],
                        delivery_target=request.get("delivery_target"),
                        scores=dict(request.get("scores", {}) or {}),
                        context=dict(request.get("context", {}) or {}),
                        metadata={
                            "brain_request_id": reply_request_id,
                            "brain_reply_kind": reply_kind,
                        },
                    ),
                )
            )

    @staticmethod
    def _prepare_executor_requests(
        *,
        requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        execute_count = sum(1 for request in requests if str(request.get("job_action", "") or "").strip() == "execute")
        if execute_count > 1:
            raise RuntimeError("single-task session runtime accepts at most one execute request per brain turn")
        prepared: list[dict[str, Any]] = []
        for request in requests:
            payload = dict(request)
            job_action = str(payload.get("job_action", "") or "").strip()
            if job_action == "execute":
                task_id = str(payload.get("task_id", "") or "").strip()
                is_new_task = task_id in {"", "new"}
                payload["_is_new_task"] = is_new_task
                if task_id in {"", "new"}:
                    task_id = f"task_{uuid4().hex[:12]}"
                payload["task_id"] = task_id
            prepared.append(payload)
        return prepared

    @staticmethod
    def _with_auto_cancel_for_replacement(
        *,
        requests: list[dict[str, Any]],
        previous_task_id: str,
    ) -> list[dict[str, Any]]:
        current_task_id = str(previous_task_id or "").strip()
        if not current_task_id:
            return requests
        execute_request = next(
            (request for request in requests if str(request.get("job_action", "") or "").strip() == "execute"),
            None,
        )
        if execute_request is None:
            return requests
        next_task_id = str(execute_request.get("task_id", "") or "").strip()
        if not next_task_id or next_task_id == current_task_id:
            return requests
        if any(
            str(request.get("job_action", "") or "").strip() == "cancel"
            and str(request.get("task_id", "") or "").strip() == current_task_id
            for request in requests
        ):
            return requests
        cancel_request = {
            "job_id": f"job_{uuid4().hex[:12]}",
            "job_action": "cancel",
            "job_kind": "execution_review",
            "task_id": current_task_id,
            "source_text": "replace_current_task",
            "request_text": "replace_current_task",
            "delivery_target": dict(execute_request.get("delivery_target", {}) or {}),
            "scores": {},
            "context": {
                "reason": "replace_current_task",
                "suppress_delivery": True,
            },
        }
        return [cancel_request, *requests]

    def _should_finalize_reply_task(
        self,
        event: BusEnvelope[BrainReplyReadyPayload],
        *,
        requests: list[dict[str, Any]],
    ) -> bool:
        if not self._is_executor_result_reply(event.payload):
            return False
        source_event = str(event.payload.metadata.get("source_event", "") or "").strip()
        if source_event not in {
            str(EventType.EXECUTOR_EVENT_JOB_REJECTED),
            str(EventType.EXECUTOR_EVENT_RESULT_READY),
        }:
            return False
        task_id = str(event.payload.related_task_id or event.task_id or "").strip()
        if not task_id:
            return False
        continued_task_ids = {
            str(item.get("task_id", "") or "").strip()
            for item in requests
            if str(item.get("job_action", "") or "").strip() == "execute" and str(item.get("task_id", "") or "").strip()
        }
        return task_id not in continued_task_ids

    async def _on_executor_result_event(
        self,
        event: BusEnvelope[ExecutorRejectedPayload | ExecutorResultPayload],
    ) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            self._task_state.apply_executor_event(context=context, event=event)
            self._world_state.apply_executor_event(session_id=session_id, event=event)
            if event.task_id:
                context.last_brain_instance_id = f"brain_executor_result_{event.task_id}"
            context.archived = self.should_archive(session_id)
            current_task_id = self.current_task_id(session_id)
            should_route = (
                bool(event.task_id)
                and str(event.task_id or "").strip() == str(current_task_id or "").strip()
            )
            brain_request = self._build_executor_result_request(event) if should_route else None
        if brain_request is not None:
            await self._bus.publish(brain_request)

    async def _on_reply_output(self, event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            self._stream_state.apply_reply_output(context=context, payload=event.payload)
            context.archived = self.should_archive(session_id)

    def _get_or_create(self, session_id: str) -> SessionContext:
        session_id = str(session_id or "").strip()
        context = self._sessions.get(session_id)
        if context is None:
            context = SessionContext(session_id=session_id)
            self._sessions[session_id] = context
        return context

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    def _build_brain_reply_request(self, event: BusEnvelope[TurnInputPayload]) -> BusEnvelope[BrainReplyRequestPayload]:
        return build_envelope(
            event_type=EventType.BRAIN_COMMAND_REPLY_REQUESTED,
            source="session",
            target="brain_runtime",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id or event.turn_id,
            causation_id=event.event_id,
            payload=BrainReplyRequestPayload(
                request_id=f"brain_req_{uuid4().hex[:12]}",
                turn_input=event.payload,
                metadata={},
            ),
        )

    def _build_executor_result_request(
        self,
        event: BusEnvelope[ExecutorRejectedPayload | ExecutorResultPayload],
    ) -> BusEnvelope[BrainReplyRequestPayload] | None:
        payload = event.payload
        executor_result = ExecutorResultContextPayload(
            source_event=str(event.event_type),
            job_id=str(payload.job_id or "").strip(),
            decision=str(getattr(payload, "decision", "") or "").strip() or "accept",
            summary=(
                str(getattr(payload, "summary", "") or getattr(payload, "reason", "") or "").strip()
                or None
            ),
            result_text=str(getattr(payload, "result_text", "") or "").strip() or None,
            reason=str(getattr(payload, "reason", "") or "").strip() or None,
            delivery_target=self._executor_result_delivery_target(task_id=event.task_id, payload=payload),
            metadata=dict(getattr(payload, "metadata", {}) or {}),
        )
        return build_envelope(
            event_type=EventType.BRAIN_COMMAND_REPLY_REQUESTED,
            source="session",
            target="brain_runtime",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.task_id or event.correlation_id or event.turn_id or payload.job_id,
            causation_id=event.event_id,
            payload=BrainReplyRequestPayload(
                request_id=f"brain_req_{uuid4().hex[:12]}",
                executor_result=executor_result,
                metadata={"executor_result": True},
            ),
        )

    @staticmethod
    def _is_executor_result_reply(payload: BrainReplyReadyPayload) -> bool:
        metadata = dict(getattr(payload, "metadata", {}) or {})
        return str(metadata.get("brain_source", "") or "").strip() == "executor_result"

    def _executor_result_delivery_target(self, *, task_id: str | None, payload: object) -> DeliveryTargetPayload:
        delivery_target = getattr(payload, "delivery_target", None)
        if delivery_target is None:
            raise RuntimeError(f"executor result event requires delivery_target: task_id={str(task_id or '').strip()}")
        return self._normalize_delivery_target(delivery_target)

    @staticmethod
    def _normalize_delivery_target(value: object) -> DeliveryTargetPayload:
        return DeliveryTargetPayload.model_validate(value)

__all__ = ["SessionRuntime"]
