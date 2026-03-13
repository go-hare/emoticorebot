"""Central execution module - 直接调用 Deep Agent 执行任务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from emoticorebot.agent.central import backend as central_backend
from emoticorebot.agent.central import stream as central_stream
from emoticorebot.agent.system import SessionTaskSystem, TaskUnit
from emoticorebot.tools import ToolRegistry
from emoticorebot.types import TaskExecutionResult, TaskSpec
from emoticorebot.utils.llm_utils import blocks_to_llm_content

if TYPE_CHECKING:
    from emoticorebot.agent.context import ContextBuilder


class CentralAgentService:
    """Central executor - 直接调用 Deep Agent，由 agent 内部处理工具循环。"""

    def __init__(
        self,
        central_llm,
        tool_registry: ToolRegistry | None,
        context_builder: "ContextBuilder",
    ):
        self.central_llm = central_llm
        self.tools = tool_registry
        self.context = context_builder
        self._agent: Any | None = None
        self._checkpointer: Any | None = None
        self._current_system: SessionTaskSystem | None = None
        self._current_task: TaskUnit | None = None
        self._trace_log: list[dict[str, Any]] = []

    async def run_task(self, task: TaskUnit, system: SessionTaskSystem) -> TaskExecutionResult:
        """执行任务 - 一次调用，Deep Agent 内部自动循环处理工具调用。"""
        if not central_backend.deep_agents_available():
            return self._build_result(
                control_state="failed",
                status="failed",
                message="Deep Agents 依赖尚未安装，central 当前无法执行内部任务。",
                analysis="系统缺少 deepagents 依赖",
                confidence=0.0,
            )

        task_spec: TaskSpec = dict(task.params or {})
        request = str(task_spec.get("request", "") or "").strip()
        if not request:
            return self._build_result(
                control_state="failed",
                status="failed",
                message="central 未收到有效请求。",
                analysis="任务请求为空",
                confidence=0.0,
            )

        agent = central_backend.ensure_agent(self)
        thread_id = self._build_thread_id(task_spec, task.task_id)
        run_id = f"run_{uuid4().hex[:12]}"

        self._current_system = system
        self._current_task = task
        self._trace_log = []

        history = [
            item for item in list(task_spec.get("history") or [])
            if isinstance(item, dict)
        ]
        media = [str(item).strip() for item in list(task_spec.get("media") or []) if str(item).strip()]
        task_context = dict(task_spec.get("task_context") or {})

        try:
            await system.report_progress(
                task, "正在执行内部任务",
                event="task.progress", producer="central", phase="stage",
            )

            agent_result = await self._invoke_agent(
                agent,
                task_spec,
                thread_id,
                run_id,
                history=history,
                media=media,
                task_context=task_context,
            )
            return self._normalize_task_result(self._extract_structured_result(agent_result))
        finally:
            self._current_system = None
            self._current_task = None

    def _build_thread_id(self, task_spec: TaskSpec, task_id: str) -> str:
        session_id = str(task_spec.get("session_id", "") or "").strip()
        if session_id:
            return f"central:{session_id}:{task_id}"
        channel = str(task_spec.get("channel", "") or "").strip()
        chat_id = str(task_spec.get("chat_id", "") or "").strip()
        base = f"{channel}:{chat_id}" if channel or chat_id else "default"
        return f"central:{base}:{task_id}"

    async def _invoke_agent(
        self,
        agent: Any,
        task_spec: TaskSpec,
        thread_id: str,
        run_id: str,
        *,
        history: list[dict[str, Any]] | None = None,
        media: list[str] | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> Any:
        """直接调用 Deep Agent - agent 内部自动处理工具循环。"""
        messages: list[dict[str, Any]] = []
        for turn in (history or [])[-10:]:
            role = str(turn.get("role", "") or "").strip()
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
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

        user_parts = [request]
        if goal:
            user_parts.append(f"\n\n任务目标：{goal}")
        if constraints:
            user_parts.append("\n\n约束条件：\n- " + "\n- ".join(constraints))
        if success_criteria:
            user_parts.append("\n\n完成标准：\n- " + "\n- ".join(success_criteria))
        if expected_output:
            user_parts.append(f"\n\n期望输出：{expected_output}")
        context_parts: list[str] = []
        top_level_history_context = str(task_spec.get("history_context", "") or "").strip()
        if top_level_history_context:
            context_parts.append(top_level_history_context)
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
            user_content: Any = [
                {"type": "text", "text": "".join(user_parts)},
                *media_items,
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            if media:
                user_parts.append(f"\n\n[附件: {', '.join(media)}]")
            messages.append({"role": "user", "content": "".join(user_parts)})

        payload = {"messages": messages}
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"assistant_id": "emoticorebot-central", "run_id": run_id},
        }

        if hasattr(agent, "astream"):
            return await self._stream_invoke(agent, payload, config)
        if hasattr(agent, "ainvoke"):
            return await agent.ainvoke(payload, config=config)
        if hasattr(agent, "invoke"):
            return agent.invoke(payload, config=config)
        raise RuntimeError("Deep Agent does not expose invoke/ainvoke/astream")

    async def _stream_invoke(
        self, agent: Any, payload: dict[str, Any], config: dict[str, Any]
    ) -> Any:
        """流式调用，捕获 trace 用于调试。"""
        last_values: Any = None
        async for item in agent.astream(
            payload,
            config=config,
            stream_mode=["values", "updates", "messages", "custom"],
            subgraphs=True,
        ):
            namespace, mode, data = central_stream.unpack_stream_item(item)
            if mode == "values":
                last_values = data
                continue
            for record in central_stream.build_trace_records(
                mode=mode, namespace=namespace, data=data
            ):
                self._trace_log.append(record)

        if last_values is None:
            raise RuntimeError("Deep Agent stream did not produce final state")
        return last_values

    def _extract_structured_result(self, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            structured = result.get("structured_response")
            if isinstance(structured, dict):
                return structured
            if "control_state" in result or "status" in result:
                return result
        raise RuntimeError("Central agent did not return a structured TaskExecutionResult")

    def _normalize_task_result(self, payload: dict[str, Any]) -> TaskExecutionResult:
        if not isinstance(payload, dict):
            raise RuntimeError("Central task result must be a dict")

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
            if not recommended_action:
                recommended_action = f"请补充以下信息：{missing[0]}"
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

        normalized: TaskExecutionResult = {
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
        return normalized

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
        if control_state == "waiting_input":
            return "pending"
        if control_state == "failed":
            return "failed"
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
            evidence = CentralAgentService._normalize_str_list(item.get("evidence"))
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


__all__ = ["CentralAgentService"]
