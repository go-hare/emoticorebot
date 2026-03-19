"""Process-local session runtime that materializes a compact session/task view."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.commands import FollowupContextPayload, LeftReplyRequestPayload, RightBrainJobRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryTargetPayload,
    LeftReplyReadyPayload,
    OutputReadyPayloadBase,
    RightBrainAcceptedPayload,
    RightBrainProgressPayload,
    RightBrainRejectedPayload,
    RightBrainResultPayload,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    TurnInputPayload,
    InputSlots,
)
from emoticorebot.protocol.topics import EventType
from emoticorebot.right_brain.store import RightBrainStore

from .models import SessionContext, SessionTaskView, SessionTraceRecord

_TRACE_LIMIT = 64


class SessionRuntime:
    """Maintains process-local session context and compact task views."""

    def __init__(self, *, bus: PriorityPubSubBus, task_store: RightBrainStore) -> None:
        self._bus = bus
        self._task_store = task_store
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
        self._bus.subscribe(consumer="session", event_type=EventType.LEFT_EVENT_REPLY_READY, handler=self._on_left_reply_ready)
        self._bus.subscribe(consumer="session", event_type=EventType.RIGHT_EVENT_JOB_ACCEPTED, handler=self._on_right_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.RIGHT_EVENT_PROGRESS, handler=self._on_right_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.RIGHT_EVENT_JOB_REJECTED, handler=self._on_right_followup_event)
        self._bus.subscribe(consumer="session", event_type=EventType.RIGHT_EVENT_RESULT_READY, handler=self._on_right_followup_event)
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
        ordered = sorted(context.tasks.values(), key=lambda item: (item.updated_at, item.task_id))
        return deepcopy(ordered)

    def task_view(self, session_id: str, task_id: str) -> SessionTaskView | None:
        context = self._get_or_create(session_id)
        view = context.tasks.get(task_id)
        return deepcopy(view) if view is not None else None

    def task_trace_summary(self, task_id: str, *, limit: int = 3) -> list[str]:
        for context in self._sessions.values():
            view = context.tasks.get(task_id)
            if view is None:
                continue
            messages = [item.message for item in view.trace if item.message]
            return messages[-limit:]
        return []

    async def consume_task_traces(self, session_id: str, task_id: str) -> list[SessionTraceRecord]:
        session_key = str(session_id or "").strip()
        task_key = str(task_id or "").strip()
        if not session_key or not task_key:
            return []
        async with self._session_lock(session_key):
            context = self._get_or_create(session_key)
            view = context.tasks.get(task_key)
            if view is None or not view.trace:
                return []
            unread = self._unread_traces(context=context, view=view)
            if unread:
                context.trace_cursor[task_key] = unread[-1].trace_id
            return deepcopy(unread)

    async def consume_task_trace_summary(self, session_id: str, task_id: str, *, limit: int = 3) -> list[str]:
        unread = await self.consume_task_traces(session_id, task_id)
        messages = [item.message for item in unread if item.message]
        if limit <= 0:
            return messages
        return messages[-limit:]

    def latest_active_task_id(self, session_id: str) -> str:
        context = self._get_or_create(session_id)
        return context.active_task_ids[-1] if context.active_task_ids else ""

    def latest_task_id(self, session_id: str) -> str:
        context = self._get_or_create(session_id)
        ordered = sorted(context.tasks.values(), key=lambda item: (item.updated_at, item.task_id))
        return ordered[-1].task_id if ordered else ""

    def clear_session(self, session_id: str) -> None:
        session_key = str(session_id or "").strip()
        self._sessions.pop(session_key, None)
        self._session_locks.pop(session_key, None)

    def should_archive(self, session_id: str) -> bool:
        context = self._get_or_create(session_id)
        return (
            not context.active_task_ids
            and not context.active_reply_stream_id
            and not context.active_input_stream_id
        )

    def update_memory_snapshot(self, session_id: str, snapshot: Mapping[str, Any] | None) -> None:
        context = self._get_or_create(session_id)
        context.memory_snapshot = dict(snapshot or {}) if snapshot is not None else None

    async def _on_user_input(self, event: BusEnvelope[TurnInputPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        left_request: BusEnvelope[LeftReplyRequestPayload] | None = None
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            context.last_turn_id = event.turn_id
            context.channel_kind = self._channel_kind(event.payload)
            if self._should_supersede_active_reply(event.payload):
                context.active_reply_stream_id = None
            context.last_user_input = self._user_text(event.payload)
            if event.turn_id:
                context.last_left_brain_instance_id = f"left_{event.turn_id}"
            context.archived = self.should_archive(session_id)
            left_request = self._build_left_reply_request(event)
        if left_request is not None:
            await self._bus.publish(left_request)

    async def _on_input_stream_started(self, event: BusEnvelope[StreamStartPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            if event.payload.stream_id in context.interrupted_input_stream_ids:
                context.archived = self.should_archive(session_id)
                return
            payload_metadata = dict(event.payload.metadata or {})
            payload_metadata.setdefault("session_mode", event.payload.session_mode)
            context.active_input_stream_id = event.payload.stream_id
            context.active_input_stream_message = event.payload.message.model_copy(deep=True)
            context.active_input_stream_metadata = payload_metadata
            context.active_input_stream_text = ""
            context.input_stream_commit_count = 0
            context.channel_kind = self._stream_channel_kind(payload_metadata, fallback=context.channel_kind or "voice")
            if self._should_supersede_active_reply_from_stream():
                context.active_reply_stream_id = None
            context.archived = self.should_archive(session_id)

    async def _on_input_stream_chunk(self, event: BusEnvelope[StreamChunkPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            if event.payload.stream_id in context.interrupted_input_stream_ids:
                return
            if context.active_input_stream_id != event.payload.stream_id:
                return
            context.active_input_stream_text += str(event.payload.chunk_text or "")
            context.archived = self.should_archive(session_id)

    async def _on_input_stream_committed(self, event: BusEnvelope[StreamCommitPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        turn_event: BusEnvelope[TurnInputPayload] | None = None
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            if event.payload.stream_id in context.interrupted_input_stream_ids:
                return
            if context.active_input_stream_id != event.payload.stream_id:
                return
            if context.active_input_stream_message is None:
                return

            commit_index = context.input_stream_commit_count + 1
            committed_text = str(event.payload.committed_text or "").strip() or str(context.active_input_stream_text or "").strip()
            if not committed_text:
                return

            stream_metadata = dict(context.active_input_stream_metadata or {})
            commit_metadata = dict(event.payload.metadata or {})
            merged_metadata = dict(stream_metadata)
            merged_metadata.update(commit_metadata)

            channel_kind = self._stream_channel_kind(merged_metadata, fallback=context.channel_kind or "voice")
            input_kind = self._stream_input_kind(merged_metadata)
            session_mode = self._stream_session_mode(merged_metadata, fallback_channel_kind=channel_kind)
            barge_in = bool(merged_metadata.get("barge_in", False))
            turn_id = str(event.turn_id or "").strip() or f"turn_stream_{event.payload.stream_id}_{commit_index}"

            turn_metadata = dict(merged_metadata)
            turn_metadata.update(
                {
                    "source_input_mode": "stream",
                    "source_stream_id": event.payload.stream_id,
                    "stream_commit": True,
                    "stream_commit_index": commit_index,
                }
            )

            context.input_stream_commit_count = commit_index
            context.active_input_stream_text = ""
            context.archived = self.should_archive(session_id)

            turn_event = build_envelope(
                event_type=EventType.INPUT_TURN_RECEIVED,
                source="session",
                target="broadcast",
                session_id=event.session_id,
                turn_id=turn_id,
                correlation_id=turn_id,
                causation_id=event.event_id,
                payload=TurnInputPayload(
                    input_id=turn_id,
                    input_mode="turn",
                    session_mode=session_mode,
                    channel_kind=channel_kind,
                    input_kind=input_kind,
                    barge_in=barge_in,
                    message=context.active_input_stream_message.model_copy(deep=True),
                    user_text=committed_text,
                    input_slots=InputSlots(),
                    metadata=turn_metadata,
                ),
            )
        if turn_event is not None:
            await self._bus.publish(turn_event)

    async def _on_input_stream_interrupted(self, event: BusEnvelope[StreamInterruptedPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            context.interrupted_input_stream_ids.add(event.payload.stream_id)
            if context.active_input_stream_id != event.payload.stream_id:
                context.archived = self.should_archive(session_id)
                return
            self._clear_active_input_stream(context)
            context.archived = self.should_archive(session_id)

    async def _on_left_reply_ready(self, event: BusEnvelope[LeftReplyReadyPayload]) -> None:
        if not event.payload.invoke_right_brain:
            return
        request = dict(event.payload.right_brain_request or {})
        job_action = str(request.get("job_action", "") or "").strip()
        if job_action not in {"create_task", "cancel_task"}:
            return
        job_id = str(request.get("job_id", "") or "").strip() or f"job_{uuid4().hex[:12]}"
        task_id = str(request.get("task_id", "") or event.payload.related_task_id or event.task_id or "").strip() or None
        await self._bus.publish(
            build_envelope(
                event_type=EventType.RIGHT_COMMAND_JOB_REQUESTED,
                source="session",
                target="right_runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=task_id,
                correlation_id=task_id or event.correlation_id or event.turn_id,
                causation_id=event.event_id,
                payload=RightBrainJobRequestPayload(
                    job_id=job_id,
                    job_action=job_action,
                    job_kind=str(request.get("job_kind", "") or "").strip() or None,
                    source_text=str(request.get("source_text", "") or "").strip() or None,
                    request_text=str(request.get("request_text", "") or "").strip() or None,
                    task_id=task_id,
                    goal=str(request.get("goal", "") or "").strip() or None,
                    delivery_target=request.get("delivery_target"),
                    scores=dict(request.get("scores", {}) or {}),
                    context=dict(request.get("context", {}) or {}),
                    metadata={
                        "left_request_id": event.payload.request_id,
                        "left_reply_kind": event.payload.reply_kind,
                    },
                ),
            )
        )

    async def _on_right_followup_event(
        self,
        event: BusEnvelope[
            RightBrainAcceptedPayload | RightBrainProgressPayload | RightBrainRejectedPayload | RightBrainResultPayload
        ],
    ) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            self._apply_right_event(context, event)
            if event.task_id:
                context.last_left_brain_instance_id = f"left_followup_{event.task_id}"
            context.archived = self.should_archive(session_id)
            left_request = self._build_followup_reply_request(event)
        await self._bus.publish(left_request)

    def _apply_right_event(
        self,
        context: SessionContext,
        event: BusEnvelope[
            RightBrainAcceptedPayload | RightBrainProgressPayload | RightBrainRejectedPayload | RightBrainResultPayload
        ],
    ) -> None:
        task_id = str(event.task_id or "").strip()
        if not task_id:
            return
        view = context.tasks.get(task_id)
        if view is None:
            view = SessionTaskView(task_id=task_id)
            context.tasks[task_id] = view
        context.trace_cursor.setdefault(task_id, "")

        self._hydrate_task_view(view)
        task = self._task_store.get(task_id)
        if task is not None:
            view.title = str(task.title or "").strip()
            view.request = str(task.request.request or "").strip()
            view.state = task.state.value
            view.result = task.result
            view.updated_at = str(task.updated_at or "").strip()
            view.summary = str(task.summary or task.last_progress or "").strip()

        trace = self._build_right_trace(event)
        if trace is not None:
            view.trace.append(trace)
            view.trace.sort(key=lambda item: (item.ts, item.trace_id))
            if len(view.trace) > _TRACE_LIMIT:
                view.trace = view.trace[-_TRACE_LIMIT:]

        context.rebuild_indexes()

    async def _on_reply_output(self, event: BusEnvelope[OutputReadyPayloadBase]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            stream_state = self._reply_stream_state(event.payload)
            stream_id = str(getattr(event.payload, "stream_id", "") or "").strip() or event.payload.content.reply_id

            if stream_state in {"open", "delta"}:
                context.active_reply_stream_id = stream_id
            elif stream_state in {"close", "superseded", "final"}:
                if context.active_reply_stream_id in {None, "", stream_id}:
                    context.active_reply_stream_id = None

            if stream_state in {"open", "delta"}:
                context.archived = self.should_archive(session_id)
                return

            text = self._reply_text(event.payload)
            if not text:
                context.archived = self.should_archive(session_id)
                return
            context.last_assistant_output = text
            context.session_summary = text
            context.archived = self.should_archive(session_id)

    def _hydrate_task_view(self, view: SessionTaskView) -> None:
        task = self._task_store.get(view.task_id)
        if task is None:
            return
        if not view.title:
            view.title = str(task.title or "").strip()
        if not view.request and task.request is not None:
            view.request = str(task.request.request or "").strip()
        if not view.updated_at:
            view.updated_at = str(task.updated_at or "").strip()

    @staticmethod
    def _unread_traces(*, context: SessionContext, view: SessionTaskView) -> list[SessionTraceRecord]:
        if not view.trace:
            return []
        cursor = str(context.trace_cursor.get(view.task_id, "") or "").strip()
        if not cursor:
            return list(view.trace)
        for index, item in enumerate(view.trace):
            if item.trace_id == cursor:
                return list(view.trace[index + 1 :])
        return list(view.trace)

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

    @staticmethod
    def _is_stale_event(*, current_updated_at: str, event_updated_at: str) -> bool:
        if not current_updated_at or not event_updated_at:
            return False
        return event_updated_at < current_updated_at

    @staticmethod
    def _reply_text(payload: OutputReadyPayloadBase) -> str:
        if payload.content.plain_text:
            return str(payload.content.plain_text).strip()
        parts = [block.text for block in payload.content.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def _reply_stream_state(payload: OutputReadyPayloadBase) -> str:
        return str(getattr(payload, "stream_state", "") or "").strip()

    @staticmethod
    def _user_text(payload: TurnInputPayload) -> str:
        if payload.user_text:
            return str(payload.user_text).strip()
        if payload.input_slots.user:
            return str(payload.input_slots.user).strip()
        parts = [block.text for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def _channel_kind(payload: TurnInputPayload) -> str:
        return str(getattr(payload, "channel_kind", "") or "chat").strip() or "chat"

    @staticmethod
    def _stream_channel_kind(metadata: Mapping[str, Any] | None, *, fallback: str) -> str:
        value = str((metadata or {}).get("channel_kind", "") or "").strip()
        if value in {"chat", "voice", "video"}:
            return value
        return fallback or "voice"

    @staticmethod
    def _stream_input_kind(metadata: Mapping[str, Any] | None) -> str:
        value = str((metadata or {}).get("input_kind", "") or "").strip()
        return value if value in {"text", "voice", "multimodal"} else "voice"

    @classmethod
    def _stream_session_mode(cls, metadata: Mapping[str, Any] | None, *, fallback_channel_kind: str) -> str:
        value = str((metadata or {}).get("session_mode", "") or "").strip()
        if value in {"turn_chat", "realtime_chat"}:
            return value
        return "turn_chat" if fallback_channel_kind == "chat" else "realtime_chat"

    def _build_left_reply_request(self, event: BusEnvelope[TurnInputPayload]) -> BusEnvelope[LeftReplyRequestPayload]:
        return build_envelope(
            event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED,
            source="session",
            target="left_runtime",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id or event.turn_id,
            causation_id=event.event_id,
            payload=LeftReplyRequestPayload(
                request_id=f"left_req_{uuid4().hex[:12]}",
                turn_input=event.payload,
                metadata={},
            ),
        )

    def _build_followup_reply_request(
        self,
        event: BusEnvelope[
            RightBrainAcceptedPayload | RightBrainProgressPayload | RightBrainRejectedPayload | RightBrainResultPayload
        ],
    ) -> BusEnvelope[LeftReplyRequestPayload]:
        payload = event.payload
        followup_context = FollowupContextPayload(
            source_event=str(event.event_type),
            job_id=str(payload.job_id or "").strip(),
            decision=str(getattr(payload, "decision", "") or "").strip() or "accept",
            stage=str(getattr(payload, "stage", "") or "").strip() or None,
            summary=(
                str(getattr(payload, "summary", "") or getattr(payload, "reason", "") or "").strip()
                or None
            ),
            progress=getattr(payload, "progress", None),
            next_step=str(getattr(payload, "next_step", "") or "").strip() or None,
            result_text=str(getattr(payload, "result_text", "") or "").strip() or None,
            reason=str(getattr(payload, "reason", "") or "").strip() or None,
            delivery_target=self._followup_delivery_target(task_id=event.task_id, payload=payload),
            metadata=dict(getattr(payload, "metadata", {}) or {}),
        )
        return build_envelope(
            event_type=EventType.LEFT_COMMAND_REPLY_REQUESTED,
            source="session",
            target="left_runtime",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.task_id or event.correlation_id or event.turn_id or payload.job_id,
            causation_id=event.event_id,
            payload=LeftReplyRequestPayload(
                request_id=f"left_req_{uuid4().hex[:12]}",
                followup_context=followup_context,
                metadata={"followup": True},
            ),
        )

    def _followup_delivery_target(self, *, task_id: str | None, payload: object) -> DeliveryTargetPayload:
        delivery_target = getattr(payload, "delivery_target", None)
        if delivery_target is None:
            raise RuntimeError(f"right followup event requires delivery_target: task_id={str(task_id or '').strip()}")
        return self._normalize_delivery_target(delivery_target)

    @staticmethod
    def _normalize_delivery_target(value: object) -> DeliveryTargetPayload:
        return DeliveryTargetPayload.model_validate(value)

    def _build_right_trace(
        self,
        event: BusEnvelope[
            RightBrainAcceptedPayload | RightBrainProgressPayload | RightBrainRejectedPayload | RightBrainResultPayload
        ],
    ) -> SessionTraceRecord | None:
        task_id = str(event.task_id or "").strip()
        if not task_id:
            return None
        payload = event.payload
        message = ""
        kind = "status"
        if event.event_type == EventType.RIGHT_EVENT_JOB_ACCEPTED:
            message = str(getattr(payload, "reason", "") or "右脑已开始处理。").strip()
            kind = "status"
        elif event.event_type == EventType.RIGHT_EVENT_PROGRESS:
            message = str(getattr(payload, "summary", "") or "").strip()
            metadata = dict(getattr(payload, "metadata", {}) or {})
            event_name = str(metadata.get("event", "") or "").strip()
            if event_name == "task.tool":
                kind = "tool"
            elif event_name == "task.trace":
                nested = metadata.get("payload")
                role = str(nested.get("role", "") or "").strip() if isinstance(nested, dict) else ""
                kind = "message" if role == "assistant" else ("tool" if role == "tool" else "progress")
            else:
                kind = "progress"
        elif event.event_type == EventType.RIGHT_EVENT_JOB_REJECTED:
            message = str(getattr(payload, "reason", "") or "右脑拒绝执行当前请求。").strip()
            kind = "warning"
        elif event.event_type == EventType.RIGHT_EVENT_RESULT_READY:
            message = str(getattr(payload, "result_text", "") or getattr(payload, "summary", "") or "").strip()
            outcome = str(getattr(payload, "metadata", {}).get("result", "") or "").strip()
            if outcome == "failed":
                kind = "error"
            elif outcome == "cancelled":
                kind = "warning"
            else:
                kind = "summary"
        if not message:
            return None
        task = self._task_store.get(task_id)
        ts = str(task.updated_at or "").strip() if task is not None else ""
        return SessionTraceRecord(
            trace_id=str(event.event_id or f"trace_{task_id}"),
            task_id=task_id,
            kind=kind,
            message=message,
            ts=ts,
            data={"source_event": str(event.event_type), **dict(getattr(payload, "metadata", {}) or {})},
        )

    @staticmethod
    def _should_supersede_active_reply(payload: TurnInputPayload) -> bool:
        barge_in = bool(getattr(payload, "barge_in", False))
        if barge_in:
            return True
        return True

    @staticmethod
    def _should_supersede_active_reply_from_stream() -> bool:
        return True

    @staticmethod
    def _clear_active_input_stream(context: SessionContext) -> None:
        context.active_input_stream_id = None
        context.active_input_stream_message = None
        context.active_input_stream_metadata = {}
        context.active_input_stream_text = ""
        context.input_stream_commit_count = 0


__all__ = ["SessionRuntime"]
