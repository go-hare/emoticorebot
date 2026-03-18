"""Protocol-native deep-agent executor used by the worker role."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from emoticorebot.right import deep_agent_backend as executor_backend
from emoticorebot.right import stream as executor_stream
from emoticorebot.right.executor_context import ExecutorContext
from emoticorebot.right.tool_runtime import AuditInterrupt, DetailedProgressReporter, ExecutionToolRuntime
from emoticorebot.protocol.task_result import TaskExecutionResult
from emoticorebot.utils.llm_utils import blocks_to_llm_content


class DeepAgentExecutor:
    """Runs a task spec against the deep-agent backend without runtime coupling."""

    DEFAULT_TASK_TIMEOUT_S = 120.0
    _JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    assistant_role = "worker"

    def __init__(self, worker_llm: Any, tool_registry: Any, context_builder: Any) -> None:
        self.executor_context = ExecutorContext(
            worker_llm=worker_llm,
            tool_registry=tool_registry,
            context_builder=context_builder,
        )
        self.tool_runtime = ExecutionToolRuntime()
        self.worker_llm = self.executor_context.worker_llm
        self.tools = self.executor_context.tool_registry
        self.context = self.executor_context.context_builder
        self._agent: Any | None = None
        self._checkpointer: Any | None = None
        self._trace_log: list[dict[str, Any]] = []

    async def execute(
        self,
        task_spec: dict[str, Any],
        *,
        task_id: str,
        progress_reporter: DetailedProgressReporter | None = None,
        trace_reporter: DetailedProgressReporter | None = None,
    ) -> TaskExecutionResult:
        if not executor_backend.deep_agents_available():
            return self._build_result(
                control_state="failed",
                status="failed",
                message="Worker agent 依赖当前不可用，worker 无法执行内部任务。",
                analysis="系统缺少 create_agent 执行能力",
                confidence=0.0,
            )

        request = str(task_spec.get("request", "") or "").strip()
        if not request:
            return self._build_result(
                control_state="failed",
                status="failed",
                message="worker 未收到有效请求。",
                analysis="任务请求为空",
                confidence=0.0,
            )

        task_profile = executor_backend.build_task_profile(task_spec)
        agent = executor_backend.build_agent(self, profile=task_profile)
        thread_id = self._build_thread_id(task_spec, task_id)
        run_id = f"run_{uuid4().hex[:12]}"
        self.tool_runtime.bind_reporter(progress_reporter)
        self._trace_log = []

        history = [item for item in list(task_spec.get("history") or []) if isinstance(item, dict)]
        media = [str(item).strip() for item in list(task_spec.get("media") or []) if str(item).strip()]
        task_context = dict(task_spec.get("task_context") or {})
        agent_task: asyncio.Task[Any] | None = None

        try:
            if progress_reporter is not None:
                await progress_reporter(
                    "正在执行内部任务",
                    {
                        "event": "task.progress",
                        "producer": "worker",
                        "phase": "stage",
                    },
                )

            timeout_s = self._resolve_task_timeout(task_spec)
            agent_task = asyncio.create_task(
                self._invoke_agent(
                    agent,
                    task_spec,
                    thread_id,
                    run_id,
                    history=history,
                    media=media,
                    task_context=task_context,
                    task_profile=task_profile,
                    trace_reporter=trace_reporter,
                ),
                name=f"deep-agent:{task_id}",
            )
            try:
                agent_result = await asyncio.wait_for(asyncio.shield(agent_task), timeout=timeout_s)
            except asyncio.TimeoutError:
                agent_task.cancel()
                agent_task.add_done_callback(self._consume_background_task_result)
                timeout_text = int(timeout_s) if float(timeout_s).is_integer() else round(timeout_s, 1)
                return self._build_result(
                    control_state="failed",
                    status="failed",
                    message=f"worker 执行超时（{timeout_text}s），本次任务已终止。",
                    analysis="worker executor 在限定时间内未返回结果。",
                    confidence=0.0,
                )
            return self._normalize_task_result(self._extract_structured_result(agent_result))
        except AuditInterrupt:
            raise
        except asyncio.CancelledError:
            if agent_task is not None and not agent_task.done():
                agent_task.cancel()
                agent_task.add_done_callback(self._consume_background_task_result)
            raise
        finally:
            self.tool_runtime.clear()

    def _build_thread_id(self, task_spec: dict[str, Any], task_id: str) -> str:
        session_id = str(task_spec.get("session_id", "") or "").strip()
        if session_id:
            return f"worker:{session_id}:{task_id}"
        channel = str(task_spec.get("channel", "") or "").strip()
        chat_id = str(task_spec.get("chat_id", "") or "").strip()
        base = f"{channel}:{chat_id}" if channel or chat_id else "default"
        return f"worker:{base}:{task_id}"

    def _resolve_task_timeout(self, task_spec: dict[str, Any]) -> float:
        raw_timeout = task_spec.get("timeout_s")
        try:
            timeout_s = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_s = self.DEFAULT_TASK_TIMEOUT_S
        if timeout_s <= 0:
            return self.DEFAULT_TASK_TIMEOUT_S
        return timeout_s

    @staticmethod
    def _consume_background_task_result(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _invoke_agent(
        self,
        agent: Any,
        task_spec: dict[str, Any],
        thread_id: str,
        run_id: str,
        *,
        history: list[dict[str, Any]] | None = None,
        media: list[str] | None = None,
        task_context: dict[str, Any] | None = None,
        task_profile: executor_backend.WorkerTaskProfile | None = None,
        trace_reporter: DetailedProgressReporter | None = None,
    ) -> Any:
        messages: list[dict[str, Any]] = []
        for turn in (history or [])[-10:]:
            role = str(turn.get("role", "") or "").strip()
            content = turn.get("content", "")
            if role in {"user", "assistant"} and content:
                llm_content = blocks_to_llm_content(content)
                if llm_content:
                    messages.append({"role": role, "content": llm_content})

        request = str(task_spec.get("request", "") or "").strip()
        goal = str(task_spec.get("goal", "") or "").strip()
        expected_output = str(task_spec.get("expected_output", "") or "").strip()
        constraints = [str(item).strip() for item in list(task_spec.get("constraints") or []) if str(item).strip()]
        success_criteria = [
            str(item).strip()
            for item in list(task_spec.get("success_criteria") or [])
            if str(item).strip()
        ]
        memory_refs = [str(item).strip() for item in list(task_spec.get("memory_refs") or []) if str(item).strip()]
        skill_hints = [str(item).strip() for item in list(task_spec.get("skill_hints") or []) if str(item).strip()]

        user_parts: list[str] = []
        if task_profile is not None and task_profile.task_hint:
            user_parts.append(task_profile.task_hint)
        user_parts.append(request)
        if goal:
            user_parts.append(f"\n\n任务目标：{goal}")
        if constraints:
            user_parts.append("\n\n约束条件：\n- " + "\n- ".join(constraints))
        if success_criteria:
            user_parts.append("\n\n完成标准：\n- " + "\n- ".join(success_criteria))
        if expected_output:
            user_parts.append(f"\n\n期望输出：{expected_output}")
        if memory_refs:
            user_parts.append("\n\n相关任务经验：\n- " + "\n- ".join(memory_refs[:6]))
        if skill_hints:
            user_parts.append("\n\n技能提示：\n- " + "\n- ".join(skill_hints[:6]))

        context_parts: list[str] = []
        history_context = str(task_spec.get("history_context", "") or "").strip()
        if history_context:
            context_parts.append(history_context)
        if task_context:
            nested_history_context = str(task_context.get("history_context", "") or "").strip()
            if nested_history_context and nested_history_context not in context_parts:
                context_parts.append(nested_history_context)
            provided_inputs = task_context.get("provided_inputs")
            if isinstance(provided_inputs, dict) and provided_inputs:
                lines = [
                    f"- {str(key).strip()}: {str(value).strip()}"
                    for key, value in provided_inputs.items()
                    if str(key).strip() and str(value).strip()
                ]
                if lines:
                    context_parts.append("用户已补充的信息：\n" + "\n".join(lines))
            follow_up_inputs = [
                str(item).strip()
                for item in list(task_context.get("follow_up_inputs", []) or [])
                if str(item).strip()
            ]
            if follow_up_inputs:
                context_parts.append("补充记录：\n- " + "\n- ".join(follow_up_inputs[-8:]))
        if context_parts:
            user_parts.append("\n\n补充上下文：\n" + "\n".join(context_parts))

        media_items = self.context.build_media_context(media) if self.context else []
        if media_items:
            messages.append({"role": "user", "content": [{"type": "text", "text": "".join(user_parts)}, *media_items]})
        else:
            if media:
                user_parts.append(f"\n\n[附件: {', '.join(media)}]")
            messages.append({"role": "user", "content": "".join(user_parts)})

        payload = {"messages": messages}
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"assistant_id": "emoticorebot-worker", "run_id": run_id},
        }

        if hasattr(agent, "astream"):
            return await self._stream_invoke(agent, payload, config, trace_reporter=trace_reporter)
        if hasattr(agent, "ainvoke"):
            return await agent.ainvoke(payload, config=config)
        if hasattr(agent, "invoke"):
            return agent.invoke(payload, config=config)
        raise RuntimeError("Deep Agent does not expose invoke/ainvoke/astream")

    async def _stream_invoke(
        self,
        agent: Any,
        payload: dict[str, Any],
        config: dict[str, Any],
        *,
        trace_reporter: DetailedProgressReporter | None = None,
    ) -> Any:
        last_values: Any = None
        async for item in agent.astream(
            payload,
            config=config,
            stream_mode=["values", "updates", "messages", "custom"],
            subgraphs=True,
        ):
            namespace, mode, data = executor_stream.unpack_stream_item(item)
            if mode == "values":
                last_values = data
                continue
            for record in executor_stream.build_trace_records(mode=mode, namespace=namespace, data=data):
                self._trace_log.append(record)
                if trace_reporter is None:
                    continue
                message = self._trace_message(record)
                if not message:
                    continue
                await trace_reporter(
                    message,
                    {
                        "event": "task.trace",
                        "producer": str(record.get("role", "") or "worker").strip() or "worker",
                        "phase": "trace",
                        "payload": dict(record),
                    },
                )
        if last_values is None:
            raise RuntimeError("Deep Agent stream did not produce final state")
        return last_values

    @staticmethod
    def _trace_message(record: dict[str, Any]) -> str:
        role = str(record.get("role", "") or "").strip()
        tool_calls = list(record.get("tool_calls", []) or [])
        if tool_calls:
            names = [
                str(item.get("name", "") or "").strip()
                for item in tool_calls
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            ]
            if names:
                return f"准备调用工具：{', '.join(names[:3])}"
        if role == "tool":
            name = str(record.get("name", "") or "tool").strip()
            content = DeepAgentExecutor._content_to_text(record.get("content"))
            if content:
                return f"{name} 返回：{content[:160]}"
            return f"{name} 已返回结果"
        if role == "assistant":
            content = DeepAgentExecutor._content_to_text(record.get("content"))
            if content:
                return content[:160]
        return ""

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = str(item.get("text", "") or item.get("content", "") or "").strip()
                    if text:
                        parts.append(text)
                        continue
                text = str(item or "").strip()
                if text:
                    parts.append(text)
            return "\n".join(parts).strip()
        if isinstance(content, dict):
            text = str(content.get("text", "") or content.get("content", "") or "").strip()
            if text:
                return text
        return str(content or "").strip()

    def _extract_structured_result(self, result: Any) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            payload = result.model_dump()
            if isinstance(payload, dict):
                return payload
        if isinstance(result, dict):
            structured = result.get("structured_response")
            if hasattr(structured, "model_dump"):
                structured = structured.model_dump()
            if isinstance(structured, dict):
                return structured
            if "control_state" in result or "status" in result:
                return result

            messages = result.get("messages", [])
            text = ""
            for msg in reversed(messages):
                content = getattr(msg, "content", None)
                if content is None:
                    content = msg.get("content", "") if isinstance(msg, dict) else ""
                if isinstance(content, list):
                    parts = [
                        str(item.get("text", "")) if isinstance(item, dict) and item.get("type") == "text" else str(item)
                        for item in content
                    ]
                    content = "\n".join(parts)
                if isinstance(content, str) and content.strip():
                    text = content.strip()
                    break

            if text:
                fence_match = self._JSON_FENCE_RE.search(text)
                if fence_match:
                    text = fence_match.group(1).strip()
                try:
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        return payload
                except json.JSONDecodeError:
                    pass

        raise RuntimeError("Worker agent did not return a structured TaskExecutionResult")

    def _normalize_task_result(self, payload: dict[str, Any]) -> TaskExecutionResult:
        if not isinstance(payload, dict):
            raise RuntimeError("Worker task result must be a dict")

        control_state = str(payload.get("control_state", "completed") or "completed").strip()
        if control_state not in {"waiting_input", "completed", "failed"}:
            raise RuntimeError(f"Invalid task control_state: {control_state!r}")

        default_status = self._default_status_for_control_state(control_state)
        status = str(payload.get("status", default_status) or default_status).strip()
        if status not in {"success", "partial", "pending", "failed"}:
            raise RuntimeError(f"Invalid task status: {status!r}")

        missing = self._normalize_str_list(payload.get("missing"))
        pending_review = self._normalize_review_items(payload.get("pending_review"))
        recommended_action = str(payload.get("recommended_action", "") or "").strip()
        message = str(payload.get("message", "") or "").strip()
        analysis = str(payload.get("analysis", "") or "").strip()

        if control_state == "waiting_input":
            if not missing:
                raise RuntimeError("TaskExecutionResult.missing is required when control_state is waiting_input")
            if status not in {"pending", "partial"}:
                raise RuntimeError("TaskExecutionResult.status must be pending or partial when waiting for input")
            request_hint = recommended_action or f"请补充以下信息：{missing[0]}"
            missing_summary = "、".join(missing)
            message = message or f"缺少继续执行所需信息：{missing_summary}。{request_hint}"
            analysis = analysis or "当前执行缺少必要输入，等待用户补充后继续。"
            recommended_action = request_hint
        elif control_state == "completed":
            if status not in {"success", "partial"}:
                raise RuntimeError("TaskExecutionResult.status must be success or partial when control_state is completed")
            if not message:
                raise RuntimeError("TaskExecutionResult.message must not be empty when control_state is completed")
        elif control_state == "failed":
            if status != "failed":
                raise RuntimeError("TaskExecutionResult.status must be failed when control_state is failed")
            if not message and not analysis:
                raise RuntimeError("TaskExecutionResult.message or analysis must explain the failure")

        attempt_count = max(
            1,
            self._coerce_int(payload.get("attempt_count"), default=1),
            self._infer_attempt_count(self._trace_log),
        )

        trace_items = [dict(item) for item in self._trace_log if isinstance(item, dict)]
        if not trace_items:
            trace_items = [dict(item) for item in list(payload.get("task_trace", []) or []) if isinstance(item, dict)]

        return {
            "control_state": control_state,
            "status": status,
            "analysis": analysis,
            "message": message,
            "missing": missing,
            "pending_review": pending_review,
            "recommended_action": recommended_action,
            "confidence": self._clamp_float(payload.get("confidence"), default=0.8, minimum=0.0, maximum=1.0),
            "attempt_count": attempt_count,
            "task_trace": trace_items,
        }

    @staticmethod
    def _build_result(
        *,
        control_state: str,
        status: str,
        message: str,
        analysis: str,
        confidence: float,
    ) -> TaskExecutionResult:
        return {
            "control_state": control_state,
            "status": status,
            "message": str(message or "").strip(),
            "analysis": str(analysis or "").strip(),
            "missing": [],
            "pending_review": [],
            "recommended_action": "",
            "confidence": max(0.0, min(1.0, float(confidence))),
            "attempt_count": 1,
            "task_trace": [],
        }

    @staticmethod
    def _default_status_for_control_state(control_state: str) -> str:
        if control_state == "failed":
            return "failed"
        if control_state == "waiting_input":
            return "pending"
        return "success"

    @staticmethod
    def _normalize_review_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            record: dict[str, Any] = {}
            for key in ("item_id", "label", "reason", "required_action"):
                text = str(item.get(key, "") or "").strip()
                if text:
                    record[key] = text
            severity = str(item.get("severity", "") or "").strip().lower()
            if severity in {"low", "medium", "high"}:
                record["severity"] = severity
            if "blocking" in item:
                record["blocking"] = bool(item.get("blocking"))
            evidence = DeepAgentExecutor._normalize_str_list(item.get("evidence"))
            if evidence:
                record["evidence"] = evidence
            payload = item.get("payload")
            if isinstance(payload, dict) and payload:
                record["payload"] = dict(payload)
            if record:
                items.append(record)
        return items[:6]

    @staticmethod
    def _normalize_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items[:8]

    @staticmethod
    def _coerce_int(value: Any, *, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
        try:
            numeric = float(value)
        except Exception:
            numeric = default
        return max(minimum, min(maximum, numeric))

    @staticmethod
    def _infer_attempt_count(trace_log: list[dict[str, Any]]) -> int:
        retry_count = sum(
            1
            for item in trace_log
            if isinstance(item, dict)
            and (
                str(item.get("type", "") or "").strip().lower() == "retry"
                or "retry" in str(item.get("event", "") or "").strip().lower()
            )
        )
        return max(1, retry_count + 1)


__all__ = ["DeepAgentExecutor"]
