"""Task-view and trace projection for SessionContext."""

from __future__ import annotations

from copy import deepcopy
from emoticorebot.executor.store import ExecutorStore
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import (
    ExecutorRejectedPayload,
    ExecutorResultPayload,
)
from emoticorebot.protocol.topics import EventType

from .models import SessionContext, SessionTaskView, SessionTraceRecord

ExecutorTerminalPayload = ExecutorRejectedPayload | ExecutorResultPayload
ExecutorTerminalEvent = BusEnvelope[ExecutorTerminalPayload]


class SessionTaskRuntime:
    """Owns task snapshots and executor trace projection for UI/session views."""

    def __init__(self, *, task_store: ExecutorStore, trace_limit: int = 64) -> None:
        self._task_store = task_store
        self._trace_limit = trace_limit

    def task_views(self, context: SessionContext) -> list[SessionTaskView]:
        ordered = self._ordered_views(context)
        return deepcopy(ordered)

    def task_view(self, context: SessionContext, task_id: str) -> SessionTaskView | None:
        view = context.tasks.get(task_id)
        return deepcopy(view) if view is not None else None

    def apply_executor_event(self, *, context: SessionContext, event: ExecutorTerminalEvent) -> None:
        task_id = str(event.task_id or "").strip()
        if not task_id:
            return
        view = context.tasks.get(task_id)
        if view is None:
            view = SessionTaskView(task_id=task_id)
            context.tasks[task_id] = view
        task = self._task_store.get(task_id)
        if task is not None:
            view.title = str(task.title or "").strip()
            view.request = str(task.request.request or "").strip()
            view.state = task.state.value
            view.result = task.result
            view.updated_at = str(task.updated_at or "").strip()
            view.summary = str(task.summary or "").strip()

        trace = self._build_trace(event)
        if trace is not None:
            view.trace.append(trace)
            view.trace.sort(key=lambda item: (item.ts, item.trace_id))
            if len(view.trace) > self._trace_limit:
                view.trace = view.trace[-self._trace_limit :]

        context.rebuild_indexes()

    def _build_trace(self, event: ExecutorTerminalEvent) -> SessionTraceRecord | None:
        task_id = str(event.task_id or "").strip()
        if not task_id:
            return None
        payload = event.payload
        message = ""
        kind = "status"
        if event.event_type == EventType.EXECUTOR_EVENT_JOB_REJECTED:
            message = str(getattr(payload, "reason", "") or "执行层拒绝执行当前请求。").strip()
            kind = "warning"
        elif event.event_type == EventType.EXECUTOR_EVENT_RESULT_READY:
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
    def _ordered_views(context: SessionContext) -> list[SessionTaskView]:
        return sorted(context.tasks.values(), key=lambda item: (item.updated_at, item.task_id))
