"""Prompt assembly helpers for brain runtime decisions."""

from __future__ import annotations

import json
from typing import Any, Callable

from emoticorebot.protocol.commands import BrainReplyRequestPayload, ExecutorResultContextPayload
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.utils.executor_projection import normalize_task_state


class BrainPromptBuilder:
    """Formats the user-visible prompt blocks consumed by the brain LLM."""

    def __init__(
        self,
        *,
        session_runtime: Any | None = None,
        task_snapshot: Callable[..., dict[str, Any]],
    ) -> None:
        self._session_runtime = session_runtime
        self._task_snapshot = task_snapshot

    async def build_user_turn_instruction(
        self,
        *,
        session_id: str,
        history_context: str,
        tasks: list[object],
        user_text: str,
        source_input_mode: str,
        current_delivery_mode: str,
        available_delivery_modes: list[str],
    ) -> str:
        lines = [
            "## 当前轮执行要求",
            "你现在要对这条用户输入做一次完整决策，并且只能输出两个文本区块：`#####user######` 和 `#####Action######`。",
            "不要输出 markdown，不要输出解释，不要输出额外文本。",
            "格式固定如下，并且必须先输出 `#####user######`，再输出 `#####Action######`：",
            "#####user######",
            "<给用户看的自然语言回复>",
            "",
            "#####Action######",
            "<一个 JSON 对象或 JSON 数组>",
            "",
            "`#####user######` 会直接发给用户，并且可能被流式输出，必须自然、简洁。",
            "`#####Action######` 只给系统看，必须是合法 JSON。",
            "如果只是简单问答、闲聊、解释、计算，输出 {\"type\":\"none\"}。",
            "如果需要执行任务，输出 {\"type\":\"execute\",\"task_id\":\"new 或当前 task_id\",\"goal\":\"...\",\"mainline\":[...],\"current_stage\":\"...\",\"current_checks\":[...]}。",
            "如果要触发浅反思，输出 {\"type\":\"reflect\",\"mode\":\"turn\"}。",
            "如果用户明确要求结束当前任务，使用 {\"type\":\"execute\",\"operation\":\"cancel\",\"task_id\":\"当前任务 id\",\"reason\":\"...\"}。",
            "如果同一轮既要继续执行也要触发浅反思，输出 JSON 数组。",
            "单任务模式下一轮最多只能有一个 execute action。",
            "凡是用户要求创建文件、修改文件、运行命令、检查环境、调用工具、生成产物，必须包含一个 execute action。",
            "不要假装任务已经完成；只要还没有经过 runtime/执行层执行，就不能在 `#####user######` 里声称文件已创建、命令已运行或结果已落盘。",
            "先读取下面的 world model，再沿着 `goal -> mainline -> current_stage -> current_checks -> last_result` 判断当前主线。",
        ]
        task_lines = await self._task_context_lines(session_id, tasks)
        world_model_block = self._world_model_context_block(session_id)
        lines.extend(
            [
                "",
                "## 当前环境",
                f"- 输入形态: {source_input_mode}",
                f"- 当前前台投递: {current_delivery_mode}",
                f"- 可用投递: {', '.join(available_delivery_modes)}",
            ]
        )
        if history_context:
            lines.extend(["", "## 最近对话摘要", history_context])
        if world_model_block:
            lines.extend(["", "## 当前 world model", world_model_block])
        if task_lines:
            lines.extend(["", "## 当前 session 任务上下文", *task_lines])
        lines.extend(["", "## 用户消息", user_text])
        return "\n".join(lines).strip()

    async def build_executor_result_instruction(
        self,
        *,
        session_id: str,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object | None,
        tasks: list[object],
        history_context: str,
    ) -> str:
        lines = [
            "## 当前轮执行要求",
            "这不是新的用户输入，而是执行层返回给主脑的运行结果。",
            "你现在必须重新做一次完整决策，并且只能输出两个文本区块：`#####user######` 和 `#####Action######`。",
            "不要输出 markdown，不要输出解释，不要输出额外文本。",
            "格式固定如下，并且必须先输出 `#####user######`，再输出 `#####Action######`：",
            "#####user######",
            "<给用户看的自然语言回复>",
            "",
            "#####Action######",
            "<一个 JSON 对象或 JSON 数组>",
            "",
            "`#####user######` 会直接发给用户。",
            "`#####Action######` 只给系统看，必须是合法 JSON。",
            "如果当前 check 已结束但任务还要继续，必须输出 execute action，并且复用当前 task_id，保持原 goal/mainline，给出新的 current_stage / current_checks。",
            "如果当前 check 失败，优先换 check，不要轻易换 goal 或 mainline。",
            "如果任务确实可以终结，输出 {\"type\":\"none\"}。",
            "是否触发浅反思，由你决定；如果要反思，输出 {\"type\":\"reflect\",\"mode\":\"turn\"}。",
            "如果同一轮既要继续执行也要触发浅反思，输出 JSON 数组。",
            "单任务模式下一轮最多只能有一个 execute action。",
            "先读取下面的 world model，再根据 executor 的终态结果判断下一步。",
        ]
        task_lines = await self._task_context_lines(session_id, tasks)
        world_model_block = self._world_model_context_block(session_id)
        result_block = self._executor_result_context_block(
            event=event,
            executor_result=executor_result,
            task=task,
        )
        lines.extend(
            [
                "",
                "## 当前环境",
                "- 输入形态: executor_result",
                f"- 当前前台投递: {str(executor_result.delivery_target.delivery_mode or '').strip() or 'inline'}",
                f"- 可用投递: {str(executor_result.delivery_target.delivery_mode or '').strip() or 'inline'}",
            ]
        )
        if history_context:
            lines.extend(["", "## 最近对话摘要", history_context])
        if world_model_block:
            lines.extend(["", "## 当前 world model", world_model_block])
        if task_lines:
            lines.extend(["", "## 当前 session 任务上下文", *task_lines])
        if result_block:
            lines.extend(["", "## 当前 executor 结果", result_block])
        return "\n".join(lines).strip()

    async def _task_context_lines(self, session_id: str, tasks: list[object]) -> list[str]:
        lines: list[str] = []
        selected_tasks = self._selected_tasks(session_id=session_id, tasks=tasks)
        for task in selected_tasks:
            title = str(getattr(task, "title", "") or getattr(task, "task_id", "")).strip()
            task_id = str(getattr(task, "task_id", "") or "").strip()
            state = str(getattr(getattr(task, "state", None), "value", "") or "").strip()
            visible_status = normalize_task_state(state) if state else "running"
            summary = str(getattr(task, "summary", "") or "").strip()
            request = ""
            if getattr(task, "request", None) is not None:
                request = str(getattr(task, "request").request or "").strip()
            item = {
                "task_id": task_id,
                "title": title,
                "status": visible_status,
                "summary": summary,
                "request": request,
            }
            trace_summary = self._task_trace_summary(task)
            if trace_summary:
                item["trace"] = trace_summary
            lines.append(f"- {item}")
        return lines

    def _selected_tasks(self, *, session_id: str, tasks: list[object]) -> list[object]:
        if not tasks:
            return []
        current_task_id = self._current_world_task_id(session_id)
        if current_task_id:
            for task in reversed(tasks):
                if str(getattr(task, "task_id", "") or "").strip() == current_task_id:
                    return [task]
        return [tasks[-1]]

    def _world_model_context_block(self, session_id: str) -> str:
        payload = self._world_model_payload(session_id)
        if not payload:
            return ""
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _executor_result_context_block(
        self,
        *,
        event: BusEnvelope[BrainReplyRequestPayload],
        executor_result: ExecutorResultContextPayload,
        task: object | None,
    ) -> str:
        payload: dict[str, Any] = {
            "task_id": str(event.task_id or "").strip() or None,
            "job_id": str(executor_result.job_id or "").strip() or None,
            "source_event": str(executor_result.source_event or "").strip() or None,
            "decision": str(executor_result.decision or "").strip() or None,
            "summary": str(executor_result.summary or "").strip() or None,
            "reason": str(executor_result.reason or "").strip() or None,
            "result_text": str(executor_result.result_text or "").strip() or None,
            "metadata": dict(executor_result.metadata or {}),
        }
        if task is not None:
            payload["task_snapshot"] = self._task_snapshot(task, session_id=event.session_id or "")
        compact = {key: value for key, value in payload.items() if value not in ("", None, [], {})}
        return json.dumps(compact, ensure_ascii=False, indent=2) if compact else ""

    def _world_model_payload(self, session_id: str) -> dict[str, Any]:
        if self._session_runtime is None or not hasattr(self._session_runtime, "world_model_snapshot"):
            return {}
        try:
            model = self._session_runtime.world_model_snapshot(session_id)
        except Exception:
            return {}
        if model is None:
            return {}

        raw = model.to_dict() if hasattr(model, "to_dict") else model
        if not isinstance(raw, dict):
            return {}

        current_task_payload: dict[str, Any] = {}
        task = raw.get("current_task")
        if isinstance(task, dict):
            current_task_payload = {
                "task_id": str(task.get("task_id", "") or "").strip(),
                "goal": str(task.get("goal", "") or "").strip(),
                "status": str(task.get("status", "") or "").strip(),
                "summary": str(task.get("summary", "") or "").strip(),
                "mainline": list(task.get("mainline", []) or []),
                "current_stage": task.get("current_stage"),
                "current_batch_id": str(task.get("current_batch_id", "") or "").strip(),
                "current_checks": list(task.get("current_checks", []) or []),
                "last_result": str(task.get("last_result", "") or "").strip(),
                "check_history": list(task.get("check_history", []) or [])[-3:],
                "artifacts": list(task.get("artifacts", []) or [])[:6],
            }
            current_task_payload = {
                key: value for key, value in current_task_payload.items() if value not in ("", None, [], {})
            }

        payload = {
            "schema_version": str(raw.get("schema_version", "") or "").strip(),
            "session_id": str(raw.get("session_id", "") or session_id or "").strip(),
            "updated_at": str(raw.get("updated_at", "") or "").strip(),
            "current_topic": str(raw.get("current_topic", "") or "").strip(),
            "current_task": current_task_payload,
        }
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    def _current_world_task_id(self, session_id: str) -> str:
        if self._session_runtime is None or not hasattr(self._session_runtime, "world_model_snapshot"):
            return ""
        try:
            model = self._session_runtime.world_model_snapshot(session_id)
        except Exception:
            return ""
        task = getattr(model, "current_task", None)
        if task is None:
            return ""
        return str(getattr(task, "task_id", "") or "").strip()

    @staticmethod
    def _task_trace_summary(task: object) -> str:
        trace_log = getattr(task, "trace_log", None)
        if not isinstance(trace_log, list):
            return ""
        messages: list[str] = []
        for item in trace_log[-2:]:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message", "") or item.get("summary", "") or "").strip()
            if message:
                messages.append(message)
        return " | ".join(messages)
