"""Execution wrapper around the agent backend."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from emoticorebot.utils.llm_utils import blocks_to_llm_content

from . import backend as agent_backend
from .hooks import AuditInterrupt, DetailedProgressReporter, RunHooks
from . import trace as agent_trace

ExecutionResult = dict[str, Any]


class ExecutionExecutor:
    DEFAULT_TASK_TIMEOUT_S = 120.0
    _JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    assistant_role = "execution"

    def __init__(self, executor_llm: Any, tool_registry: Any, context_builder: Any) -> None:
        self.run_hooks = RunHooks()
        self.executor_llm = executor_llm
        self.tools = tool_registry
        self.context = context_builder
        self._trace_log: list[dict[str, Any]] = []

    async def execute(
        self,
        task_spec: dict[str, Any],
        *,
        task_id: str,
        progress_reporter: DetailedProgressReporter | None = None,
        trace_reporter: DetailedProgressReporter | None = None,
    ) -> ExecutionResult:
        if not agent_backend.backend_available():
            raise RuntimeError("execution backend is unavailable")

        request = str(task_spec.get("request", "") or "").strip()
        if not request:
            raise RuntimeError("execution request is empty")

        agent = agent_backend.build_agent(self)
        thread_id = self._build_thread_id(task_spec, task_id)
        run_id = f"run_{uuid4().hex[:12]}"
        self.run_hooks.bind_reporter(progress_reporter)
        self._trace_log = []
        agent_task = asyncio.create_task(
            self._invoke_agent(
                agent,
                task_spec,
                thread_id,
                run_id,
                history=[item for item in list(task_spec.get("history") or []) if isinstance(item, dict)],
                media=[str(item).strip() for item in list(task_spec.get("media") or []) if str(item).strip()],
                task_context=dict(task_spec.get("task_context") or {}),
                trace_reporter=trace_reporter,
            ),
            name=f"execution:{task_id}",
        )
        try:
            result = await asyncio.wait_for(asyncio.shield(agent_task), timeout=self._resolve_task_timeout(task_spec))
            return self._normalize_task_result(self._extract_structured_result(result))
        except AuditInterrupt:
            raise
        finally:
            if agent_task.done():
                self._consume_background_task_result(agent_task)
            self.run_hooks.clear()

    def _build_thread_id(self, task_spec: dict[str, Any], task_id: str) -> str:
        session_id = str(task_spec.get("session_id", "") or "").strip()
        if session_id:
            return f"execution:{session_id}:{task_id}"
        channel = str(task_spec.get("channel", "") or "").strip()
        chat_id = str(task_spec.get("chat_id", "") or "").strip()
        base = f"{channel}:{chat_id}" if channel or chat_id else "default"
        return f"execution:{base}:{task_id}"

    def _resolve_task_timeout(self, task_spec: dict[str, Any]) -> float:
        raw_timeout = task_spec.get("timeout_s")
        try:
            timeout_s = float(raw_timeout)
        except (TypeError, ValueError):
            timeout_s = self.DEFAULT_TASK_TIMEOUT_S
        if timeout_s <= 0:
            raise RuntimeError("execution timeout must be positive")
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
        history: list[dict[str, Any]],
        media: list[str],
        task_context: dict[str, Any],
        trace_reporter: DetailedProgressReporter | None,
    ) -> Any:
        messages: list[dict[str, Any]] = []
        for turn in history[-10:]:
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
        success_criteria = [str(item).strip() for item in list(task_spec.get("success_criteria") or []) if str(item).strip()]
        memory_refs = [str(item).strip() for item in list(task_spec.get("memory_refs") or []) if str(item).strip()]
        skill_hints = [str(item).strip() for item in list(task_spec.get("skill_hints") or []) if str(item).strip()]

        user_parts: list[str] = [request]
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
        nested_history_context = str(task_context.get("history_context", "") or "").strip()
        if nested_history_context and nested_history_context not in context_parts:
            context_parts.append(nested_history_context)
        if context_parts:
            user_parts.append("\n\n补充上下文：\n" + "\n".join(context_parts))

        media_items = self.context.build_media_context(media) if self.context else []
        if media_items:
            messages.append({"role": "user", "content": [{"type": "text", "text": "".join(user_parts)}, *media_items]})
        else:
            messages.append({"role": "user", "content": "".join(user_parts)})

        payload = {"messages": messages}
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"assistant_id": "emoticorebot-execution", "run_id": run_id},
        }

        if hasattr(agent, "astream"):
            return await self._stream_invoke(agent, payload, config, trace_reporter=trace_reporter)
        if hasattr(agent, "ainvoke"):
            return await agent.ainvoke(payload, config=config)
        if hasattr(agent, "invoke"):
            return agent.invoke(payload, config=config)
        raise RuntimeError("execution agent does not expose invoke/ainvoke/astream")

    async def _stream_invoke(
        self,
        agent: Any,
        payload: dict[str, Any],
        config: dict[str, Any],
        *,
        trace_reporter: DetailedProgressReporter | None,
    ) -> Any:
        last_values: Any = None
        async for item in agent.astream(
            payload,
            config=config,
            stream_mode=["values", "updates", "messages", "custom"],
            subgraphs=True,
        ):
            namespace, mode, data = agent_trace.parse_stream_item(item)
            if mode == "values":
                last_values = data
                continue
            for record in agent_trace.build_trace_records(mode=mode, namespace=namespace, data=data):
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
                        "producer": str(record.get("role", "") or "execution").strip() or "execution",
                        "phase": "trace",
                        "payload": dict(record),
                    },
                )
        if last_values is None:
            raise RuntimeError("execution trace stream did not produce final state")
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
            content = ExecutionExecutor._content_to_text(record.get("content"))
            return f"{name} 返回：{content[:160]}" if content else f"{name} 已返回结果"
        if role == "assistant":
            content = ExecutionExecutor._content_to_text(record.get("content"))
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
            return str(content.get("text", "") or content.get("content", "") or "").strip()
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

            text = ""
            for msg in reversed(list(result.get("messages", []) or [])):
                content = getattr(msg, "content", None)
                if content is None:
                    content = msg.get("content", "") if isinstance(msg, dict) else ""
                if isinstance(content, list):
                    content = "\n".join(
                        str(item.get("text", "")) if isinstance(item, dict) and item.get("type") == "text" else str(item)
                        for item in content
                    )
                if isinstance(content, str) and content.strip():
                    text = content.strip()
                    break
            if text:
                fence_match = self._JSON_FENCE_RE.search(text)
                if fence_match:
                    text = fence_match.group(1).strip()
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return payload

        raise RuntimeError("execution executor did not return a structured execution result")

    def _normalize_task_result(self, payload: dict[str, Any]) -> ExecutionResult:
        control_state = str(payload.get("control_state", "completed") or "completed").strip()
        if control_state not in {"completed", "failed"}:
            raise RuntimeError(f"Invalid task control_state: {control_state!r}")

        default_status = "failed" if control_state == "failed" else "success"
        status = str(payload.get("status", default_status) or default_status).strip()
        if status not in {"success", "partial", "failed"}:
            raise RuntimeError(f"Invalid task status: {status!r}")

        message = str(payload.get("message", "") or "").strip()
        analysis = str(payload.get("analysis", "") or "").strip()
        if control_state == "completed" and not message:
            raise RuntimeError("Execution result.message must not be empty when control_state is completed")
        if control_state == "failed" and not message and not analysis:
            raise RuntimeError("Execution failures must explain the reason")

        trace_items = [dict(item) for item in self._trace_log if isinstance(item, dict)]
        if not trace_items:
            trace_items = [dict(item) for item in list(payload.get("task_trace", []) or []) if isinstance(item, dict)]

        return {
            "control_state": control_state,
            "status": status,
            "analysis": analysis,
            "message": message,
            "task_trace": trace_items,
        }


__all__ = ["ExecutionExecutor"]
