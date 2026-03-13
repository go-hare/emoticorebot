"""Minimal two-slot task runtime with one-way queue output."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from emoticorebot.types import (
    ReviewItem,
    TaskControlState,
    TaskExecutionResult,
    TaskInputRequest,
    TaskLifecycleState,
    TaskResultStatus,
    TaskSpec,
    TaskState,
    TraceItem,
)

TaskWorker = Callable[["TaskUnit", "SessionTaskSystem"], Awaitable[Any]]

if TYPE_CHECKING:
    from emoticorebot.agent.context import ContextBuilder
    from emoticorebot.agent.central.central import CentralAgentService
    from emoticorebot.tools import ToolRegistry


@dataclass
class TaskUnit:
    task_id: str
    title: str = ""
    params: TaskSpec = field(default_factory=dict)
    worker: TaskWorker | None = None
    status: TaskLifecycleState = "running"
    summary: str = ""
    error: str = ""
    missing: list[str] = field(default_factory=list)
    input_request: TaskInputRequest | None = None
    input_fut: asyncio.Future | None = None
    runner: asyncio.Task | None = None
    result: Any = None
    stage_info: str = ""
    # 结构化结果字段（从 TaskExecutionResult 同步）
    control_state: TaskControlState = "running"
    result_status: TaskResultStatus = "pending"
    analysis: str = ""
    pending_review: list[ReviewItem] = field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 1.0
    attempt_count: int = 1
    task_trace: list[TraceItem] = field(default_factory=list)

    def snapshot(self) -> TaskState:
        return {
            "invoked": True,
            "task_id": self.task_id,
            "title": self.title,
            "params": dict(self.params),
            "status": self.status,
            "result_status": self.result_status,
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
    
    def sync_from_result(self, result: TaskExecutionResult | dict[str, Any]) -> None:
        """从 TaskExecutionResult 同步结构化字段到 TaskUnit"""
        if not isinstance(result, dict):
            return

        self.control_state = str(result.get("control_state", "running") or "running")
        self.result_status = str(result.get("status", "pending") or "pending")
        message = str(result.get("message", "") or "").strip()
        if message:
            self.summary = message
        self.analysis = str(result.get("analysis", "") or "")
        self.missing = list(result.get("missing", []) or [])
        self.pending_review = list(result.get("pending_review", []) or [])
        self.recommended_action = str(result.get("recommended_action", "") or "")
        self.task_trace = list(result.get("task_trace", []) or [])

        try:
            self.confidence = float(result.get("confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.confidence = 1.0

        try:
            self.attempt_count = int(result.get("attempt_count", 1) or 1)
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
        params: TaskSpec | None = None,
        title: str = "",
    ) -> TaskUnit:
        task = TaskUnit(
            task_id=str(task_id or "").strip(),
            title=str(title or "").strip(),
            params=dict(params or {}),
            worker=worker,
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
        task_spec: TaskSpec,
    ) -> TaskUnit:
        params: TaskSpec = {
            "task_id": str(task_spec.get("task_id", "") or "").strip(),
            "origin_message_id": str(task_spec.get("origin_message_id", "") or "").strip(),
            "title": str(task_spec.get("title", "") or "").strip(),
            "request": str(task_spec.get("request", "") or "").strip(),
            "goal": str(task_spec.get("goal", "") or "").strip(),
            "expected_output": str(task_spec.get("expected_output", "") or "").strip(),
            "history_context": str(task_spec.get("history_context", "") or "").strip(),
            "channel": str(task_spec.get("channel", "") or "").strip(),
            "chat_id": str(task_spec.get("chat_id", "") or "").strip(),
            "session_id": str(task_spec.get("session_id", "") or "").strip(),
            "constraints": [str(item).strip() for item in list(task_spec.get("constraints", []) or []) if str(item).strip()],
            "success_criteria": [
                str(item).strip() for item in list(task_spec.get("success_criteria", []) or []) if str(item).strip()
            ],
            "memory_bundle_ids": [
                str(item).strip() for item in list(task_spec.get("memory_bundle_ids", []) or []) if str(item).strip()
            ],
            "skill_hints": [str(item).strip() for item in list(task_spec.get("skill_hints", []) or []) if str(item).strip()],
            "media": [str(item).strip() for item in list(task_spec.get("media", []) or []) if str(item).strip()],
        }
        history = task_spec.get("history")
        if isinstance(history, list):
            params["history"] = [dict(item) for item in history if isinstance(item, dict)]
        task_context = task_spec.get("task_context")
        if isinstance(task_context, dict):
            params["task_context"] = dict(task_context)

        task_id = str(params.get("task_id", "") or "").strip()
        title = str(params.get("title", "") or "").strip()
        if not task_id:
            raise RuntimeError("TaskSpec.task_id is required")
        if not params.get("request"):
            raise RuntimeError("TaskSpec.request is required")

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
                if isinstance(result, dict) and "control_state" in result:
                    # 同步结构化字段到 TaskUnit
                    task.sync_from_result(result)
                    
                    # TaskExecutionResult 结构化结果
                    if task.control_state == "waiting_input" and task.missing:
                        task.input_request = {
                            "field": task.missing[0] if task.missing else "",
                            "question": task.recommended_action or "请补充以下信息",
                        }
                        task.input_fut = None
                        await self._activate_or_queue_waiting_task(task)
                        return
                    elif task.control_state == "failed":
                        await self.fail_task(
                            task,
                            reason=str(result.get("message", "") or "").strip() or "执行失败",
                        )
                        return
                    # completed 或其他状态，正常完成
                    summary = str(result.get("message", "") or "").strip()
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
            "message_id": str(task.params.get("origin_message_id", "") or "").strip(),
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
        task.control_state = "waiting_input"
        task.result_status = "pending"
        await self._activate_or_queue_waiting_task(task)

        return await task.input_fut

    async def answer(
        self,
        content: str,
        task_id: str | None = None,
        *,
        origin_message_id: str = "",
    ) -> bool:
        task = self.get_task(task_id or "") if task_id else self.waiting_task()
        if task is None or task.status != "waiting_input":
            return False

        if origin_message_id:
            task.params["origin_message_id"] = str(origin_message_id or "").strip()

        self._merge_answer_into_task(task, content)

        if task.input_fut is not None:
            if not task.input_fut.done():
                task.input_fut.set_result(content)

            task.status = "running"
            task.control_state = "running"
            task.result_status = "pending"
            task.error = ""
            task.input_fut = None
            task.input_request = None
            task.missing = []
            await self._promote_blocked_input()
            return True

        if task.worker is None:
            return False

        task.status = "running"
        task.control_state = "running"
        task.result_status = "pending"
        task.error = ""
        task.input_fut = None
        task.missing = []
        task.input_request = None
        task.stage_info = ""
        await self._start_task(task, task.worker)
        await self._promote_blocked_input()
        return True

    async def finish_task(self, task: TaskUnit, summary: str = "") -> None:
        was_waiting = task.status == "waiting_input"
        task.status = "done"
        task.control_state = "completed"
        if task.result_status not in {"success", "partial"}:
            task.result_status = "success"
        task.summary = str(summary or "").strip()
        task.error = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None
        
        # 构建事件，使用 TaskUnit 上已同步的结构化字段
        event_data: dict[str, Any] = {
            "type": "done",
            "title": task.title,
            "params": dict(task.params),
            "summary": task.summary,
            "control_state": task.control_state,
            "result_status": task.result_status,
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
        task.result_status = "failed"
        task.error = str(reason or "").strip()
        task.summary = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None
        
        # 构建事件，包含结构化字段
        event_data: dict[str, Any] = {
            "type": "failed",
            "title": task.title,
            "params": dict(task.params),
            "reason": task.error,
            "control_state": task.control_state,
            "result_status": task.result_status,
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
        blocked.control_state = "waiting_input"
        blocked.status = "waiting_input"
        await self._emit_need_input(
            blocked,
        )

    async def _activate_or_queue_waiting_task(self, task: TaskUnit) -> None:
        active_waiting = self.waiting_task()
        if active_waiting is not None and active_waiting.task_id != task.task_id:
            task.status = "blocked_input"
            return
        task.control_state = "waiting_input"
        task.status = "waiting_input"
        await self._emit_need_input(task)

    async def _emit_need_input(self, task: TaskUnit) -> None:
        input_request = dict(task.input_request or {})
        await self.emit(
            task,
            type="need_input",
            title=task.title,
            params=dict(task.params),
            field=str(input_request.get("field", "") or "").strip(),
            question=str(input_request.get("question", "") or "").strip(),
            summary=task.summary,
            message=task.summary,
            control_state="waiting_input",
            result_status=task.result_status,
            missing=list(task.missing),
            analysis=task.analysis,
            confidence=task.confidence,
            attempt_count=task.attempt_count,
            pending_review=list(task.pending_review),
            recommended_action=task.recommended_action,
            task_trace=list(task.task_trace),
        )

    @staticmethod
    def _merge_answer_into_task(task: TaskUnit, content: str) -> None:
        answer = str(content or "").strip()
        if not answer:
            return

        input_request = dict(task.input_request or {})
        field = str(input_request.get("field", "") or "").strip()
        question = str(input_request.get("question", "") or "").strip()

        history = [dict(item) for item in list(task.params.get("history", []) or []) if isinstance(item, dict)]
        if question:
            history.append({"role": "assistant", "content": question})
        history.append({"role": "user", "content": answer})
        task.params["history"] = history

        task_context = dict(task.params.get("task_context") or {})
        provided_inputs = dict(task_context.get("provided_inputs") or {})
        if field:
            provided_inputs[field] = answer
        if provided_inputs:
            task_context["provided_inputs"] = provided_inputs

        follow_up_inputs = list(task_context.get("follow_up_inputs", []) or [])
        follow_up_note = f"{field}: {answer}" if field else answer
        if follow_up_note and follow_up_note not in follow_up_inputs:
            follow_up_inputs.append(follow_up_note)
        if follow_up_inputs:
            task_context["follow_up_inputs"] = follow_up_inputs[-8:]

        task.params["task_context"] = task_context

        existing_context = str(task.params.get("history_context", "") or "").strip()
        answer_line = (
            f"用户补充信息 - {field}: {answer}"
            if field
            else f"用户补充信息: {answer}"
        )
        context_lines = [line for line in [existing_context, answer_line] if line]
        task.params["history_context"] = "\n".join(context_lines)


__all__ = ["SessionTaskSystem", "TaskUnit"]
