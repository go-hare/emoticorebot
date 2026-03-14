"""Per-session live task runtime."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from emoticorebot.protocol.events import TaskEvent
from emoticorebot.protocol.task_models import TaskSpec
from emoticorebot.runtime.input_gate import InputGate
from emoticorebot.runtime.running_task import RunningTask, TaskWorker
from emoticorebot.runtime.task_state import RuntimeTaskState

if TYPE_CHECKING:
    from emoticorebot.agent.context import ContextBuilder
    from emoticorebot.execution.central_executor import CentralExecutor
    from emoticorebot.tools import ToolRegistry


class SessionRuntime:
    """Owns all live task execution state for a single session."""

    def __init__(
        self,
        *,
        session_id: str = "",
        thread_id: str = "",
        central_llm: Any | None = None,
        context_builder: "ContextBuilder | None" = None,
        tool_registry: "ToolRegistry | None" = None,
    ):
        self.session_id = str(session_id or "").strip()
        self.thread_id = str(thread_id or self.session_id or "").strip()
        self._task_states: dict[str, RuntimeTaskState] = {}
        self._running_tasks: dict[str, RunningTask] = {}
        self.input_gate = InputGate()
        self.event_queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self.to_main_queue = self.event_queue
        self.central_llm = central_llm
        self.context = context_builder
        self.tools = tool_registry
        self._executor: "CentralExecutor | None" = None
        self._on_progress: Callable[[str], Awaitable[None]] | None = None
        self._recent_task_snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _get_executor(self) -> "CentralExecutor":
        if self._executor is None:
            if self.central_llm is None or self.context is None:
                raise RuntimeError("central execution dependencies are not configured")
            from emoticorebot.execution.central_executor import CentralExecutor

            self._executor = CentralExecutor(self.central_llm, self.tools, self.context)
        return self._executor

    def tasks(self) -> list[RunningTask]:
        return list(self._running_tasks.values())

    def active_tasks(self) -> list[RunningTask]:
        return [task for task in self._running_tasks.values() if task.status not in {"done", "failed", "cancelled"}]

    def active_task_snapshots(self) -> list[dict[str, Any]]:
        return [task.snapshot() for task in self.active_tasks()]

    def latest_active_task_snapshot(self) -> dict[str, Any] | None:
        active = self.active_tasks()
        if not active:
            return None
        return active[-1].snapshot()

    def recent_task_snapshots(self) -> list[dict[str, Any]]:
        return [dict(snapshot) for snapshot in self._recent_task_snapshots.values() if isinstance(snapshot, dict)]

    def latest_task_snapshot(self) -> dict[str, Any] | None:
        active = self.latest_active_task_snapshot()
        if active:
            return active
        if not self._recent_task_snapshots:
            return None
        last_key = next(reversed(self._recent_task_snapshots))
        snapshot = self._recent_task_snapshots.get(last_key)
        return dict(snapshot) if isinstance(snapshot, dict) else None

    def get_task_snapshot(self, task_id: str) -> dict[str, Any] | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        live_task = self.get_task(wanted)
        if live_task is not None:
            return live_task.snapshot()
        snapshot = self._recent_task_snapshots.get(wanted)
        return dict(snapshot) if isinstance(snapshot, dict) else None

    def set_progress_handler(self, handler: Callable[[str], Awaitable[None]] | None) -> None:
        self._on_progress = handler

    def is_idle(self) -> bool:
        return not self._running_tasks and self.event_queue.empty()

    def waiting_task(self) -> RunningTask | None:
        task_id = self.input_gate.current_waiting()
        return self.get_task(task_id or "")

    def blocked_task(self) -> RunningTask | None:
        task_id = self.input_gate.current_blocked()
        return self.get_task(task_id or "")

    def get_task(self, task_id: str) -> RunningTask | None:
        wanted = str(task_id or "").strip()
        if not wanted:
            return None
        return self._running_tasks.get(wanted)

    def find_task_by_title(self, title: str) -> RunningTask | None:
        wanted = str(title or "").strip().lower()
        if not wanted:
            return None
        for task in self._running_tasks.values():
            if task.title.lower() == wanted:
                return task
        for task in self._running_tasks.values():
            if wanted in task.title.lower():
                return task
        return None

    async def create_task(
        self,
        task_id: str,
        worker: TaskWorker,
        params: TaskSpec | None = None,
        title: str = "",
    ) -> RunningTask:
        task = RunningTask(
            task_id=str(task_id or "").strip(),
            title=str(title or "").strip(),
            params=dict(params or {}),
            worker=worker,
        )
        if not task.task_id:
            raise RuntimeError("task_id is required")

        self._remember_task(task)

        await self.emit(
            task,
            type="created",
            title=task.title,
            params=dict(task.params),
        )
        await self._start_task(task, worker)
        return task

    async def create_central_task(self, task_spec: TaskSpec) -> RunningTask:
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
        timeout_override = task_spec.get("timeout_s")
        try:
            timeout_s = float(timeout_override)
        except (TypeError, ValueError):
            timeout_s = 0.0
        if timeout_s > 0:
            params["timeout_s"] = timeout_s
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

        async def _worker(task: RunningTask, runtime: "SessionRuntime") -> Any:
            return await self._get_executor().run_task(task, runtime)

        return await self.create_task(task_id=task_id, worker=_worker, params=params, title=title)

    def update_params(self, task: RunningTask, **params: Any) -> None:
        task.params.update(params)

    async def _start_task(self, task: RunningTask, worker: TaskWorker) -> asyncio.Task:
        if task.runner is not None and not task.runner.done():
            raise RuntimeError("task is already running")

        async def _run() -> None:
            try:
                result = await worker(task, self)
                task.result = result

                if isinstance(result, dict) and "control_state" in result:
                    task.sync_from_result(result)

                    if task.control_state == "waiting_input" and task.missing:
                        task.input_request = {
                            "field": task.missing[0] if task.missing else "",
                            "question": task.recommended_action or "请补充以下信息",
                        }
                        task.input_fut = None
                        await self._activate_or_queue_waiting_task(task)
                        return
                    if task.control_state == "failed":
                        await self.fail_task(
                            task,
                            reason=str(result.get("message", "") or "").strip() or "执行失败",
                        )
                        return
                    summary = str(result.get("message", "") or "").strip()
                else:
                    summary = str(result or "").strip()

                if task.status in {"running", "blocked_input"}:
                    await self.finish_task(task, summary=summary)
            except asyncio.CancelledError:
                if task.status in {"running", "waiting_input", "blocked_input"}:
                    await self.fail_task(task, reason="cancelled")
                raise
            except Exception as exc:
                await self.fail_task(task, reason=str(exc))

        task.mark_started()
        task.runner = asyncio.create_task(_run(), name=f"session-runtime:{task.task_id}")
        await self.emit(
            task,
            type="started",
            params=dict(task.params),
        )
        return task.runner

    async def emit(self, task: RunningTask, /, **payload: Any) -> None:
        event: TaskEvent = {
            "task_id": task.task_id,
            "channel": str(task.params.get("channel", "") or "").strip(),
            "chat_id": str(task.params.get("chat_id", "") or "").strip(),
            "message_id": str(task.params.get("origin_message_id", "") or "").strip(),
            **dict(payload),
        }
        await self.to_main_queue.put(event)

    async def report_progress(self, task: RunningTask, message: str, **payload: Any) -> None:
        task.stage_info = str(message or "").strip()
        event: TaskEvent = {
            "type": "progress",
            "message": task.stage_info,
        }
        if payload:
            event["payload"] = dict(payload)
        await self.emit(task, **event)
        if self._on_progress is not None and task.stage_info:
            try:
                await self._on_progress(task.stage_info)
            except Exception:
                pass

    async def request_input(self, task: RunningTask, field: str, question: str) -> str:
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
            await self._promote_after_input_release(task.task_id)
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
        await self._promote_after_input_release(task.task_id)
        return True

    async def finish_task(self, task: RunningTask, summary: str = "") -> None:
        was_waiting = task.status == "waiting_input"
        promoted_id = self.input_gate.release(task.task_id) if was_waiting else None
        if not was_waiting:
            self.input_gate.remove(task.task_id)
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

        event_data: TaskEvent = {
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

        self._remember_recent_snapshot(task)
        task.missing = []
        await self.emit(task, **event_data)
        self._forget_task(task.task_id)
        if promoted_id:
            await self._promote_task(promoted_id)

    async def fail_task(self, task: RunningTask, reason: str = "") -> None:
        was_waiting = task.status == "waiting_input"
        promoted_id = self.input_gate.release(task.task_id) if was_waiting else None
        if not was_waiting:
            self.input_gate.remove(task.task_id)
        task.status = "failed"
        task.control_state = "failed"
        task.result_status = "failed"
        task.error = str(reason or "").strip()
        task.summary = ""
        if task.input_fut is not None and not task.input_fut.done():
            task.input_fut.cancel()
        task.input_fut = None
        task.input_request = None

        event_data: TaskEvent = {
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

        self._remember_recent_snapshot(task)
        task.missing = []
        await self.emit(task, **event_data)
        self._forget_task(task.task_id)
        if promoted_id:
            await self._promote_task(promoted_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "tasks": {task_id: state.snapshot() for task_id, state in self._task_states.items()},
        }

    def get_tasks_summary(self) -> str:
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

    async def _activate_or_queue_waiting_task(self, task: RunningTask) -> None:
        if not self.input_gate.activate_or_block(task.task_id):
            task.status = "blocked_input"
            return
        task.control_state = "waiting_input"
        task.status = "waiting_input"
        await self._emit_need_input(task)

    async def _emit_need_input(self, task: RunningTask) -> None:
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

    async def _promote_after_input_release(self, task_id: str) -> None:
        promoted_id = self.input_gate.release(task_id)
        if promoted_id:
            await self._promote_task(promoted_id)

    async def _promote_task(self, task_id: str) -> None:
        promoted_id = str(task_id or "").strip()
        if not promoted_id:
            return
        promoted = self.get_task(promoted_id)
        if promoted is None or promoted.input_request is None:
            return
        promoted.control_state = "waiting_input"
        promoted.status = "waiting_input"
        await self._emit_need_input(promoted)

    def _remember_task(self, task: RunningTask) -> None:
        self._running_tasks[task.task_id] = task
        self._task_states[task.task_id] = task.state
        self._remember_recent_snapshot(task)

    def _forget_task(self, task_id: str) -> None:
        self._running_tasks.pop(task_id, None)
        self._task_states.pop(task_id, None)

    def _remember_recent_snapshot(self, task: RunningTask) -> None:
        self._recent_task_snapshots[task.task_id] = task.snapshot()
        self._recent_task_snapshots.move_to_end(task.task_id)
        while len(self._recent_task_snapshots) > 32:
            self._recent_task_snapshots.popitem(last=False)

    @staticmethod
    def _merge_answer_into_task(task: RunningTask, content: str) -> None:
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
        answer_line = f"用户补充信息 - {field}: {answer}" if field else f"用户补充信息: {answer}"
        context_lines = [line for line in [existing_context, answer_line] if line]
        task.params["history_context"] = "\n".join(context_lines)


__all__ = ["SessionRuntime"]
