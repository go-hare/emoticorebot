"""Central execution module - 直接调用 Deep Agent 执行任务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from emoticorebot.agent.central import backend as central_backend
from emoticorebot.agent.central import stream as central_stream
from emoticorebot.agent.central.result import CentralResult, parse_agent_response
from emoticorebot.agent.system import SessionTaskSystem, TaskUnit
from emoticorebot.tools import ToolRegistry
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

    async def run_task(self, task: TaskUnit, system: SessionTaskSystem) -> CentralResult:
        """执行任务 - 一次调用，Deep Agent 内部自动循环处理工具调用。"""
        if not central_backend.deep_agents_available():
            result = CentralResult()
            result.control_state = "failed"
            result.status = "failed"
            result.message = "Deep Agents 依赖尚未安装，central 当前无法执行内部任务。"
            result.analysis = "系统缺少 deepagents 依赖"
            result.confidence = 0.0
            return result

        params = dict(task.params or {})
        request = str(params.get("request", "") or "").strip()
        if not request:
            result = CentralResult()
            result.control_state = "failed"
            result.status = "failed"
            result.message = "central 未收到有效请求。"
            result.analysis = "任务请求为空"
            result.confidence = 0.0
            return result

        agent = central_backend.ensure_agent(self)
        thread_id = self._build_thread_id(params, task.task_id)
        run_id = f"run_{uuid4().hex[:12]}"

        self._current_system = system
        self._current_task = task
        self._trace_log = []

        history = [
            item for item in list(params.get("history") or [])
            if isinstance(item, dict)
        ]
        media = list(params.get("media") or [])
        task_context = dict(params.get("task_context") or {})

        try:
            await system.report_progress(
                task, "正在执行内部任务",
                event="task.progress", producer="central", phase="stage",
            )

            agent_result = await self._invoke_agent(
                agent, request, thread_id, run_id,
                history=history, media=media, task_context=task_context,
            )
            raw_message = self._extract_response(agent_result)
            
            # 解析为结构化结果
            return parse_agent_response(raw_message, self._trace_log)
        finally:
            self._current_system = None
            self._current_task = None

    def _build_thread_id(self, params: dict[str, Any], task_id: str) -> str:
        session_id = str(params.get("session_id", "") or "").strip()
        if session_id:
            return f"central:{session_id}:{task_id}"
        channel = str(params.get("channel", "") or "").strip()
        chat_id = str(params.get("chat_id", "") or "").strip()
        base = f"{channel}:{chat_id}" if channel or chat_id else "default"
        return f"central:{base}:{task_id}"

    async def _invoke_agent(
        self,
        agent: Any,
        request: str,
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

        user_parts = [request]
        if task_context:
            ctx_text = str(task_context.get("history_context", "") or "").strip()
            if ctx_text:
                user_parts.append(f"\n\n补充上下文：{ctx_text}")

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

    def _extract_response(self, result: Any) -> str:
        """从 agent 结果中提取最终回复文本。"""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            messages = result.get("messages")
            if isinstance(messages, list) and messages:
                last = messages[-1]
                if isinstance(last, dict):
                    return str(last.get("content", "") or "")
                content = getattr(last, "content", "")
                if isinstance(content, list):
                    return " ".join(str(item) for item in content if item)
                return str(content or "")
        content = getattr(result, "content", "")
        if content:
            return str(content)
        return str(result)


__all__ = ["CentralAgentService"]
