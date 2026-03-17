"""Process-local session runtime that materializes a compact session/task view."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import ReplyReadyPayload, UserMessagePayload
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

    def register(self) -> None:
        self._bus.subscribe(consumer="session", event_type=EventType.INPUT_USER_MESSAGE, handler=self._on_user_input)
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
        self._sessions.pop(str(session_id or "").strip(), None)

    async def _on_user_input(self, event: BusEnvelope[UserMessagePayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        context = self._get_or_create(session_id)
        context.last_turn_id = event.turn_id
        context.last_user_input = self._user_text(event.payload)

    async def _on_reply_output(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        session_id = str(event.session_id or "").strip()
        if not session_id:
            return
        stream_state = str(event.payload.reply.metadata.get("stream_state", "") or "").strip()
        if stream_state == "delta":
            return
        text = self._reply_text(event.payload)
        if not text:
            return
        context = self._get_or_create(session_id)
        context.last_assistant_output = text

    async def _on_task_event(self, event: BusEnvelope[object]) -> None:
        session_id = str(event.session_id or "").strip()
        task_id = str(event.task_id or getattr(event.payload, "task_id", "") or "").strip()
        if not session_id or not task_id:
            return

        context = self._get_or_create(session_id)
        view = context.tasks.get(task_id)
        if view is None:
            view = SessionTaskView(task_id=task_id)
            context.tasks[task_id] = view

        self._hydrate_task_view(view)
        self._apply_task_event(context, view, event)
        context.rebuild_indexes()

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
            context.trace_cursor[view.task_id] = view.trace[-1].trace_id

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

    def _get_or_create(self, session_id: str) -> SessionContext:
        session_id = str(session_id or "").strip()
        context = self._sessions.get(session_id)
        if context is None:
            context = SessionContext(session_id=session_id)
            self._sessions[session_id] = context
        return context

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
    def _user_text(payload: UserMessagePayload) -> str:
        if payload.plain_text:
            return str(payload.plain_text).strip()
        parts = [block.text for block in payload.content_blocks if block.type == "text" and block.text]
        return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()


__all__ = ["SessionRuntime"]
