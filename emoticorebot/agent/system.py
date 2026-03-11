"""Minimal two-slot task runtime with one-way queue output."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

TaskWorker = Callable[["TaskUnit", "SessionTaskSystem"], Awaitable[Any]]

if TYPE_CHECKING:
    from emoticorebot.agent.context import ContextBuilder
    from emoticorebot.agent.central.central import CentralAgentService
    from emoticorebot.tools import ToolRegistry


@dataclass
class TaskUnit:
    task_id: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    summary: str = ""
    error: str = ""
    missing: list[str] = field(default_factory=list)
    input_request: dict[str, Any] | None = None
    input_fut: asyncio.Future | None = None
    runner: asyncio.Task | None = None
    result: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "params": dict(self.params),
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "missing": list(self.missing),
            "input_request": dict(self.input_request or {}),
        }


class SessionTaskSystem:
    def __init__(
        self,
        *,
        central_llm: Any | None = None,
        context_builder: "ContextBuilder | None" = None,
        tool_registry: "ToolRegistry | None" = None,
    ):
        self.current: TaskUnit | None = None
        self.prev: TaskUnit | None = None
        self.to_main_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.central_llm = central_llm
        self.context = context_builder
        self.tools = tool_registry
        self._central: "CentralAgentService | None" = None

    def _get_central(self) -> "CentralAgentService":
        if self._central is None:
            if self.central_llm is None or self.context is None:
                raise RuntimeError("central execution dependencies are not configured")
            from emoticorebot.agent.central.central import CentralAgentService

            self._central = CentralAgentService(self.central_llm, self.tools, self.context)
        return self._central

    def tasks(self) -> list[TaskUnit]:
        return [task for task in (self.prev, self.current) if task is not None]

    def active_tasks(self) -> list[TaskUnit]:
        return [task for task in self.tasks() if task.status not in {"done", "failed"}]

    def waiting_task(self) -> TaskUnit | None:
        for task in (self.prev, self.current):
            if task is not None and task.status == "waiting_input":
                return task
        return None

    def blocked_task(self) -> TaskUnit | None:
        for task in (self.prev, self.current):
            if task is not None and task.status == "blocked_input":
                return task
        return None

    def get_task(self, task_id: str) -> TaskUnit | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        for task in (self.prev, self.current):
            if task is not None and task.task_id == wanted:
                return task
        return None

    async def create_task(
        self,
        task_id: str,
        worker: TaskWorker,
        params: dict[str, Any] | None = None,
    ) -> TaskUnit:
        if len(self.active_tasks()) >= 2:
            raise RuntimeError("at most two active tasks")

        task = TaskUnit(task_id=str(task_id or "").strip(), params=dict(params or {}))
        if not task.task_id:
            raise RuntimeError("task_id is required")

        if self.current is None or self.current.status in {"done", "failed"}:
            self.current = task
        elif self.prev is None or self.prev.status in {"done", "failed"}:
            self.prev = self.current
            self.current = task
        else:
            raise RuntimeError("at most two active tasks")

        await self.emit(
            task,
            type="created",
            params=dict(task.params),
        )
        await self._start_task(task, worker)
        return task

    async def create_central_task(
        self,
        task_id: str,
        *,
        request: str,
        history: list[dict[str, Any]] | None = None,
        task_context: dict[str, Any] | None = None,
        media: list[str] | None = None,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
        extra_params: dict[str, Any] | None = None,
    ) -> TaskUnit:
        params = {
            "request": str(request or "").strip(),
            "history": [dict(item) for item in list(history or []) if isinstance(item, dict)],
            "task_context": dict(task_context or {}),
            "media": list(media or []),
            "channel": str(channel or "").strip(),
            "chat_id": str(chat_id or "").strip(),
            "session_id": str(session_id or "").strip(),
        }
        if extra_params:
            params.update(dict(extra_params))

        async def _worker(task: TaskUnit, system: "SessionTaskSystem") -> Any:
            return await self._get_central().run_task(task, system)

        return await self.create_task(task_id=task_id, worker=_worker, params=params)

    def update_params(self, task: TaskUnit, **params: Any) -> None:
        task.params.update(params)

    async def _start_task(self, task: TaskUnit, worker: TaskWorker) -> asyncio.Task:
        if task.runner is not None and not task.runner.done():
            raise RuntimeError("task is already running")

        async def _run() -> None:
            try:
                result = await worker(task, self)
                task.result = result
                if task.status in {"running", "blocked_input"}:
                    await self.finish_task(task, summary=str(result or "").strip())
            except asyncio.CancelledError:
                if task.status in {"running", "waiting_input", "blocked_input"}:
                    await self.fail_task(task, reason="cancelled")
                raise
            except Exception as exc:
                await self.fail_task(task, reason=str(exc))

        task.runner = asyncio.create_task(_run(), name=f"task-unit:{task.task_id}")
        await self.emit(
            task,
            type="started",
            params=dict(task.params),
        )
        return task.runner

    async def emit(self, task: TaskUnit, /, **payload: Any) -> None:
        event = {
            "task_id": task.task_id,
            **dict(payload),
        }
        await self.to_main_queue.put(event)

    async def report_progress(self, task: TaskUnit, message: str, **payload: Any) -> None:
        event: dict[str, Any] = {
            "type": "progress",
            "message": str(message or "").strip(),
        }
        if payload:
            event["payload"] = dict(payload)
        await self.emit(task, **event)

    async def request_input(self, task: TaskUnit, field: str, question: str) -> str:
        loop = asyncio.get_running_loop()
        task.input_fut = loop.create_future()
        task.input_request = {
            "field": str(field or "").strip(),
            "question": str(question or "").strip(),
        }
        task.missing = [task.input_request["field"]] if task.input_request["field"] else []

        if self.waiting_task() is None:
            task.status = "waiting_input"
            await self.emit(
                task,
                type="need_input",
                field=task.input_request["field"],
                question=task.input_request["question"],
            )
        else:
            task.status = "blocked_input"

        return await task.input_fut

    async def answer(self, content: str, task_id: str | None = None) -> bool:
        task = self.get_task(task_id or "") if task_id else self.waiting_task()
        if task is None or task.input_fut is None or task.status != "waiting_input":
            return False

        if not task.input_fut.done():
            task.input_fut.set_result(content)

        task.status = "running"
        task.input_fut = None
        task.input_request = None
        task.missing = []
        await self._promote_blocked_input()
        return True

    async def finish_task(self, task: TaskUnit, summary: str = "") -> None:
        was_waiting = task.status == "waiting_input"
        task.status = "done"
        task.summary = str(summary or "").strip()
        task.error = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None
        task.missing = []
        await self.emit(
            task,
            type="done",
            summary=task.summary,
        )
        if was_waiting:
            await self._promote_blocked_input()

    async def fail_task(self, task: TaskUnit, reason: str = "") -> None:
        was_waiting = task.status == "waiting_input"
        task.status = "failed"
        task.error = str(reason or "").strip()
        task.summary = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None
        task.missing = []
        await self.emit(
            task,
            type="failed",
            reason=task.error,
        )
        if was_waiting:
            await self._promote_blocked_input()

    def snapshot(self) -> dict[str, Any]:
        return {
            "current": self.current.snapshot() if self.current is not None else None,
            "prev": self.prev.snapshot() if self.prev is not None else None,
        }

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        raw = dict(snapshot or {})
        self.current = self._restore_task(raw.get("current"))
        self.prev = self._restore_task(raw.get("prev"))

    @staticmethod
    def _restore_task(raw: Any) -> TaskUnit | None:
        if not isinstance(raw, dict):
            return None
        task_id = str(raw.get("task_id", "") or "").strip()
        if not task_id:
            return None
        task = TaskUnit(task_id=task_id, params=dict(raw.get("params") or {}))
        task.status = str(raw.get("status", "running") or "running").strip() or "running"
        task.summary = str(raw.get("summary", "") or "").strip()
        task.error = str(raw.get("error", "") or "").strip()
        task.missing = [str(item).strip() for item in list(raw.get("missing", []) or []) if str(item).strip()]
        task.input_request = dict(raw.get("input_request") or {}) if isinstance(raw.get("input_request"), dict) else None
        return task

    async def _promote_blocked_input(self) -> None:
        blocked = self.blocked_task()
        if blocked is None or blocked.input_request is None:
            return
        blocked.status = "waiting_input"
        await self.emit(
            blocked,
            type="need_input",
            field=str(blocked.input_request.get("field", "") or "").strip(),
            question=str(blocked.input_request.get("question", "") or "").strip(),
        )


__all__ = ["SessionTaskSystem", "TaskUnit"]
