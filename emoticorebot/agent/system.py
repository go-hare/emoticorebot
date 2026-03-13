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
    title: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    summary: str = ""
    error: str = ""
    missing: list[str] = field(default_factory=list)
    input_request: dict[str, Any] | None = None
    input_fut: asyncio.Future | None = None
    runner: asyncio.Task | None = None
    result: Any = None
    stage_info: str = ""
    # 结构化结果字段（从 CentralResult 同步）
    control_state: str = "running"
    analysis: str = ""
    pending_review: list[dict[str, Any]] = field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 1.0
    attempt_count: int = 1
    task_trace: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "params": dict(self.params),
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "missing": list(self.missing),
            "input_request": dict(self.input_request or {}),
            "stage_info": self.stage_info,
            "control_state": self.control_state,
            "analysis": self.analysis,
            "pending_review": list(self.pending_review),
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "attempt_count": self.attempt_count,
            "task_trace": list(self.task_trace),
        }
    
    def sync_from_result(self, result: Any) -> None:
        """从 CentralResult 同步结构化字段到 TaskUnit"""
        if not hasattr(result, "to_dict"):
            return
        
        self.control_state = str(getattr(result, "control_state", "running") or "running")
        self.analysis = str(getattr(result, "analysis", "") or "")
        self.missing = list(getattr(result, "missing", []) or [])
        self.pending_review = list(getattr(result, "pending_review", []) or [])
        self.recommended_action = str(getattr(result, "recommended_action", "") or "")
        self.task_trace = list(getattr(result, "task_trace", []) or [])
        
        try:
            self.confidence = float(getattr(result, "confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.confidence = 1.0
        
        try:
            self.attempt_count = int(getattr(result, "attempt_count", 1) or 1)
        except (TypeError, ValueError):
            self.attempt_count = 1


class SessionTaskSystem:
    def __init__(
        self,
        *,
        central_llm: Any | None = None,
        context_builder: "ContextBuilder | None" = None,
        tool_registry: "ToolRegistry | None" = None,
    ):
        self._tasks: dict[str, TaskUnit] = {}
        self.to_main_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.central_llm = central_llm
        self.context = context_builder
        self.tools = tool_registry
        self._central: "CentralAgentService | None" = None
        self._on_progress: Callable[[str], Awaitable[None]] | None = None

    def _get_central(self) -> "CentralAgentService":
        if self._central is None:
            if self.central_llm is None or self.context is None:
                raise RuntimeError("central execution dependencies are not configured")
            from emoticorebot.agent.central.central import CentralAgentService

            self._central = CentralAgentService(self.central_llm, self.tools, self.context)
        return self._central

    def tasks(self) -> list[TaskUnit]:
        return list(self._tasks.values())

    def active_tasks(self) -> list[TaskUnit]:
        return [task for task in self._tasks.values() if task.status not in {"done", "failed"}]

    def waiting_task(self) -> TaskUnit | None:
        for task in self._tasks.values():
            if task.status == "waiting_input":
                return task
        return None

    def blocked_task(self) -> TaskUnit | None:
        for task in self._tasks.values():
            if task.status == "blocked_input":
                return task
        return None

    def get_task(self, task_id: str) -> TaskUnit | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        return self._tasks.get(wanted)

    def find_task_by_title(self, title: str) -> TaskUnit | None:
        wanted = str(title or "").strip().lower()
        if not wanted:
            return None
        for task in self._tasks.values():
            if task.title.lower() == wanted:
                return task
        for task in self._tasks.values():
            if wanted in task.title.lower():
                return task
        return None

    async def create_task(
        self,
        task_id: str,
        worker: TaskWorker,
        params: dict[str, Any] | None = None,
        title: str = "",
    ) -> TaskUnit:
        task = TaskUnit(
            task_id=str(task_id or "").strip(),
            title=str(title or "").strip(),
            params=dict(params or {}),
        )
        if not task.task_id:
            raise RuntimeError("task_id is required")

        self._tasks[task.task_id] = task

        await self.emit(
            task,
            type="created",
            title=task.title,
            params=dict(task.params),
        )
        await self._start_task(task, worker)
        return task

    async def create_central_task(
        self,
        task_id: str,
        *,
        title: str = "",
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

        return await self.create_task(task_id=task_id, worker=_worker, params=params, title=title)

    def update_params(self, task: TaskUnit, **params: Any) -> None:
        task.params.update(params)

    async def _start_task(self, task: TaskUnit, worker: TaskWorker) -> asyncio.Task:
        if task.runner is not None and not task.runner.done():
            raise RuntimeError("task is already running")

        async def _run() -> None:
            try:
                result = await worker(task, self)
                task.result = result
                
                # 处理结构化结果
                if hasattr(result, "control_state"):
                    # 同步结构化字段到 TaskUnit
                    task.sync_from_result(result)
                    
                    # CentralResult 结构化结果
                    if result.control_state == "waiting_input" and result.missing:
                        # 需要补充信息，触发 need_input 事件
                        task.status = "waiting_input"
                        await self.emit(
                            task,
                            type="need_input",
                            field=result.missing[0] if result.missing else "",
                            question=result.recommended_action or "请补充以下信息",
                            missing=list(result.missing),
                            analysis=result.analysis,
                            confidence=result.confidence,
                        )
                        return
                    elif result.control_state == "failed":
                        await self.fail_task(task, reason=result.message or "执行失败")
                        return
                    # completed 或其他状态，正常完成
                    summary = result.message or str(result or "").strip()
                else:
                    # 字符串结果（向后兼容）
                    summary = str(result or "").strip()
                
                if task.status in {"running", "blocked_input"}:
                    await self.finish_task(task, summary=summary)
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
            "channel": str(task.params.get("channel", "") or "").strip(),
            "chat_id": str(task.params.get("chat_id", "") or "").strip(),
            "message_id": str(task.params.get("message_id", "") or "").strip(),
            **dict(payload),
        }
        await self.to_main_queue.put(event)

    async def report_progress(self, task: TaskUnit, message: str, **payload: Any) -> None:
        task.stage_info = str(message or "").strip()
        event: dict[str, Any] = {
            "type": "progress",
            "message": task.stage_info,
        }
        if payload:
            event["payload"] = dict(payload)
        await self.emit(task, **event)
        # 同时调用直连模式的进度回调（如果存在）
        if self._on_progress is not None and task.stage_info:
            try:
                await self._on_progress(task.stage_info)
            except Exception:
                pass

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
        task.control_state = "completed"
        task.summary = str(summary or "").strip()
        task.error = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None
        
        # 构建事件，使用 TaskUnit 上已同步的结构化字段
        event_data: dict[str, Any] = {
            "type": "done",
            "summary": task.summary,
            "control_state": task.control_state,
            "analysis": task.analysis,
            "missing": list(task.missing),
            "pending_review": list(task.pending_review),
            "recommended_action": task.recommended_action,
            "confidence": task.confidence,
            "attempt_count": task.attempt_count,
            "task_trace": list(task.task_trace),
        }
        
        # 完成后清空 missing（因为任务已完成）
        task.missing = []
        
        await self.emit(task, **event_data)
        self._tasks.pop(task.task_id, None)
        if was_waiting:
            await self._promote_blocked_input()

    async def fail_task(self, task: TaskUnit, reason: str = "") -> None:
        was_waiting = task.status == "waiting_input"
        task.status = "failed"
        task.control_state = "failed"
        task.error = str(reason or "").strip()
        task.summary = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None
        
        # 构建事件，包含结构化字段
        event_data: dict[str, Any] = {
            "type": "failed",
            "reason": task.error,
            "control_state": task.control_state,
            "analysis": task.analysis,
            "missing": list(task.missing),
            "recommended_action": task.recommended_action,
            "confidence": task.confidence,
            "attempt_count": task.attempt_count,
            "task_trace": list(task.task_trace),
        }
        
        task.missing = []
        await self.emit(task, **event_data)
        self._tasks.pop(task.task_id, None)
        if was_waiting:
            await self._promote_blocked_input()

    def snapshot(self) -> dict[str, Any]:
        return {
            "tasks": {tid: t.snapshot() for tid, t in self._tasks.items()},
        }

    def get_tasks_summary(self) -> str:
        """获取所有任务的摘要信息，用于回复用户查询。"""
        tasks = self.active_tasks()
        if not tasks:
            return "当前没有正在执行的任务。"
        lines = []
        for task in tasks:
            status_text = {
                "running": "执行中",
                "waiting_input": "等待补充信息",
                "blocked_input": "排队等待",
            }.get(task.status, task.status)
            line = f"- {task.title or task.task_id}: {status_text}"
            if task.stage_info:
                line += f" ({task.stage_info})"
            lines.append(line)
        return "\n".join(lines)

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
