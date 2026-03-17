"""Process-local session runtime that materializes a compact session/task view."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Mapping

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload, StableInputPayload
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType, Topic
from emoticorebot.runtime.task_store import TaskStore

from .models import SessionContext, SessionTaskView, SessionTraceRecord

_RUNNING_EVENTS = {str(EventType.TASK_UPDATE), str(EventType.TASK_SUMMARY)}
_TRACE_KINDS = {
    str(EventType.TASK_UPDATE): "progress",
    str(EventType.TASK_SUMMARY): "summary",
    str(EventType.TASK_ASK): "ask",
}
_TRACE_LIMIT = 64


class SessionRuntime:
    """Maintains process-local session context and compact task views."""

    def __init__(self, *, bus: PriorityPubSubBus, task_store: TaskStore) -> None:
        self._bus = bus
        self._task_store = task_store
        self._sessions: dict[str, SessionContext] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    def register(self) -> None:
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_STABLE, handler=self._on_user_input)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_REPLY_APPROVED, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=self._on_reply_output)
        self._bus.subscribe(consumer="session", topic=Topic.TASK_EVENT, handler=self._on_task_event)

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

    def latest_waiting_task_id(self, session_id: str) -> str:
        context = self._get_or_create(session_id)
        return context.waiting_task_ids[-1] if context.waiting_task_ids else ""

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
        return not context.active_task_ids and not context.waiting_task_ids and not context.active_reply_stream_id

    def update_memory_snapshot(self, session_id: str, snapshot: Mapping[str, Any] | None) -> None:
        context = self._get_or_create(session_id)
        context.memory_snapshot = dict(snapshot or {}) if snapshot is not None else None

    async def _on_user_input(self, event: BusEnvelope[StableInputPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            context.last_turn_id = event.turn_id
            context.channel_kind = self._channel_kind(event.payload)
            task_origin = self._is_task_origin_input(event.payload)
            if self._should_supersede_active_reply(event.payload):
                context.active_reply_stream_id = None
            if not task_origin:
                context.last_user_input = self._user_text(event.payload)
            if event.turn_id:
                context.last_front_instance_id = (
                    f"front_task_{self._task_origin_task_id(event.payload) or event.turn_id}"
                    if task_origin
                    else f"front_{event.turn_id}"
                )
            context.archived = self.should_archive(session_id)

    async def _on_reply_output(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            reply_metadata = dict(event.payload.reply.metadata or {})
            stream_state = str(reply_metadata.get("stream_state", "") or "").strip()
            stream_id = str(reply_metadata.get("stream_id", "") or "").strip() or event.payload.reply.reply_id

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

    async def _on_task_event(self, event: BusEnvelope[object]) -> None:
        session_id = str(event.session_id or "").strip()
        task_id = str(event.task_id or getattr(event.payload, "task_id", "") or "").strip()
        if not session_id or not task_id:
            return

        trigger_event: BusEnvelope[StableInputPayload] | None = None
        async with self._session_lock(session_id):
            context = self._get_or_create(session_id)
            view = context.tasks.get(task_id)
            if view is None:
                view = SessionTaskView(task_id=task_id)
                context.tasks[task_id] = view
            context.trace_cursor.setdefault(task_id, "")

            self._hydrate_task_view(view)
            self._apply_task_event(context, view, event)
            context.rebuild_indexes()
            if str(event.event_type) in {str(EventType.TASK_ASK), str(EventType.TASK_END)}:
                context.last_front_instance_id = f"front_task_{task_id}"
                trigger_event = self._build_task_front_trigger(context=context, view=view, event=event)
            context.archived = self.should_archive(session_id)
        if trigger_event is not None:
            await self._bus.publish(trigger_event)

    def _apply_task_event(self, context: SessionContext, view: SessionTaskView, event: BusEnvelope[object]) -> None:
        payload = event.payload
        event_type = str(event.event_type)
        event_updated_at = str(getattr(payload, "updated_at", "") or "").strip()
        is_stale = self._is_stale_event(current_updated_at=view.updated_at, event_updated_at=event_updated_at)

        question = str(getattr(payload, "question", "") or "").strip()
        if question and self._should_update_latest_ask(current_updated_at=view.latest_ask_at, event_updated_at=event_updated_at):
            view.latest_ask = question
            view.latest_ask_field = str(getattr(payload, "field", "") or "").strip()
            view.latest_ask_at = event_updated_at

        if not is_stale:
            summary = str(
                getattr(payload, "summary", "")
                or getattr(payload, "message", "")
                or getattr(payload, "output", "")
                or getattr(payload, "error", "")
                or ""
            ).strip()
            if summary:
                view.summary = summary

            if event_updated_at:
                view.updated_at = event_updated_at

            if event_type == str(EventType.TASK_ASK):
                view.state = "waiting"
                view.result = "none"
            elif event_type == str(EventType.TASK_END):
                view.state = "done"
                view.result = str(getattr(payload, "result", "none") or "none").strip() or "none"
            elif event_type in _RUNNING_EVENTS:
                view.state = "running"
                view.result = "none"

        traces = self._build_traces(event)
        if traces:
            view.trace.extend(traces)
            view.trace.sort(key=lambda item: (item.ts, item.trace_id))
            if len(view.trace) > _TRACE_LIMIT:
                view.trace = view.trace[-_TRACE_LIMIT:]

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

    def _build_traces(self, event: BusEnvelope[object]) -> list[SessionTraceRecord]:
        payload = event.payload
        session_id = str(event.session_id or "").strip()
        task_id = str(event.task_id or getattr(payload, "task_id", "") or "").strip()
        if not task_id:
            return []
        raw_items = (
            list(getattr(payload, "trace_final", []) or [])
            if str(event.event_type) == str(EventType.TASK_END)
            else list(getattr(payload, "trace_append", []) or [])
        )
        traces = [
            trace
            for index, item in enumerate(raw_items)
            if (trace := self._trace_from_item(session_id=session_id, task_id=task_id, item=item, fallback_id=f"{event.event_id}:{index}"))
            is not None
        ]
        if traces:
            return traces
        fallback = self._build_trace(event)
        return [fallback] if fallback is not None else []

    def _build_trace(self, event: BusEnvelope[object]) -> SessionTraceRecord | None:
        payload = event.payload
        task_id = str(event.task_id or getattr(payload, "task_id", "") or "").strip()
        if not task_id:
            return None
        message = self._task_event_message(event)
        if not message:
            return None
        return SessionTraceRecord(
            trace_id=str(event.event_id or f"trace_{task_id}"),
            task_id=task_id,
            kind=self._trace_kind(event),
            message=message,
            ts=str(getattr(payload, "updated_at", "") or "").strip(),
            data={},
        )

    @staticmethod
    def _trace_from_item(
        *,
        session_id: str,
        task_id: str,
        item: object,
        fallback_id: str,
    ) -> SessionTraceRecord | None:
        data: Mapping[str, Any] | None = None
        if isinstance(item, Mapping):
            trace_id = str(item.get("trace_id", "") or "").strip() or fallback_id
            kind = str(item.get("kind", "") or "").strip()
            message = str(item.get("message", "") or "").strip()
            ts = str(item.get("ts", "") or "").strip()
            raw_data = item.get("data", {})
            if isinstance(raw_data, Mapping):
                data = raw_data
        else:
            trace_id = str(getattr(item, "trace_id", "") or "").strip() or fallback_id
            kind = str(getattr(item, "kind", "") or "").strip()
            message = str(getattr(item, "message", "") or "").strip()
            ts = str(getattr(item, "ts", "") or "").strip()
            raw_data = getattr(item, "data", {})
            if isinstance(raw_data, Mapping):
                data = raw_data
        if not message:
            return None
        payload = dict(data or {})
        payload.setdefault("task_id", task_id)
        if session_id:
            payload.setdefault("session_id", session_id)
        return SessionTraceRecord(
            trace_id=trace_id,
            task_id=task_id,
            kind=kind or "info",
            message=message,
            ts=ts,
            data=payload,
        )

    @staticmethod
    def _task_event_message(event: BusEnvelope[object]) -> str:
        payload = event.payload
        event_type = str(event.event_type)
        if event_type == str(EventType.TASK_UPDATE):
            return str(getattr(payload, "message", "") or "").strip()
        if event_type == str(EventType.TASK_SUMMARY):
            return str(getattr(payload, "summary", "") or "").strip()
        if event_type == str(EventType.TASK_ASK):
            return str(getattr(payload, "question", "") or getattr(payload, "why", "") or "").strip()
        if event_type == str(EventType.TASK_END):
            return str(
                getattr(payload, "summary", "")
                or getattr(payload, "output", "")
                or getattr(payload, "error", "")
                or ""
            ).strip()
        return ""

    @staticmethod
    def _trace_kind(event: BusEnvelope[object]) -> str:
        if str(event.event_type) != str(EventType.TASK_END):
            return _TRACE_KINDS.get(str(event.event_type), "status")
        result = str(getattr(event.payload, "result", "") or "").strip()
        if result == "success":
            return "summary"
        if result == "cancelled":
            return "warning"
        return "error"

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
    def _should_update_latest_ask(*, current_updated_at: str, event_updated_at: str) -> bool:
        if not current_updated_at or not event_updated_at:
            return True
        return event_updated_at >= current_updated_at

    @staticmethod
    def _reply_text(payload: ReplyReadyPayload) -> str:
        if payload.reply.plain_text:
            return str(payload.reply.plain_text).strip()
        parts = [block.text for block in payload.reply.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def _user_text(payload: StableInputPayload) -> str:
        if payload.plain_text:
            return str(payload.plain_text).strip()
        parts = [block.text for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()

    @staticmethod
    def _channel_kind(payload: StableInputPayload) -> str:
        channel_kind = str(getattr(payload, "channel_kind", "") or "").strip()
        if channel_kind:
            return channel_kind
        metadata = getattr(payload, "metadata", {})
        if isinstance(metadata, Mapping):
            return str(metadata.get("channel_kind", "") or "chat").strip() or "chat"
        return "chat"

    def _build_task_front_trigger(
        self,
        *,
        context: SessionContext,
        view: SessionTaskView,
        event: BusEnvelope[object],
    ) -> BusEnvelope[StableInputPayload] | None:
        task_id = str(view.task_id or "").strip()
        if not task_id:
            return None
        task = self._task_store.get(task_id)
        origin = (
            getattr(task, "origin_message", None)
            if task is not None and getattr(task, "origin_message", None) is not None
            else MessageRef(channel="system", chat_id=context.session_id, message_id=f"task_prompt_{task_id}")
        )
        task_event_type = str(event.event_type)
        prompt_text = self._task_event_message(event)
        metadata: dict[str, Any] = {
            "front_origin": "task",
            "task_event_type": task_event_type,
            "task_event_id": event.event_id,
            "task_id": task_id,
            "task_result": str(getattr(event.payload, "result", "") or "").strip(),
            "task_question": str(getattr(event.payload, "question", "") or "").strip(),
            "task_field": str(getattr(event.payload, "field", "") or "").strip(),
            "task_why": str(getattr(event.payload, "why", "") or "").strip(),
            "task_summary": str(getattr(event.payload, "summary", "") or "").strip(),
            "task_output": str(getattr(event.payload, "output", "") or "").strip(),
            "task_error": str(getattr(event.payload, "error", "") or "").strip(),
            "channel_kind": context.channel_kind or "chat",
        }
        turn_id = context.last_turn_id or event.turn_id or f"turn_task_{task_id}"
        return build_envelope(
            event_type=EventType.INPUT_STABLE,
            source="session",
            target="broadcast",
            session_id=context.session_id,
            turn_id=turn_id,
            task_id=task_id,
            correlation_id=event.correlation_id or task_id or turn_id,
            causation_id=event.event_id,
            payload=StableInputPayload(
                input_id=f"task_front_{task_id}_{event.event_id[-8:]}",
                input_kind="text",
                channel_kind=context.channel_kind or "chat",
                message=origin,
                plain_text=prompt_text or f"task event {task_event_type}",
                metadata=metadata,
            ),
        )

    @staticmethod
    def _is_task_origin_input(payload: StableInputPayload) -> bool:
        metadata = getattr(payload, "metadata", {})
        return isinstance(metadata, Mapping) and str(metadata.get("front_origin", "") or "").strip() == "task"

    @staticmethod
    def _task_origin_task_id(payload: StableInputPayload) -> str:
        metadata = getattr(payload, "metadata", {})
        if not isinstance(metadata, Mapping):
            return ""
        return str(metadata.get("task_id", "") or "").strip()

    @staticmethod
    def _should_supersede_active_reply(payload: StableInputPayload) -> bool:
        barge_in = bool(getattr(payload, "barge_in", False))
        if barge_in:
            return True
        return True


__all__ = ["SessionRuntime"]
