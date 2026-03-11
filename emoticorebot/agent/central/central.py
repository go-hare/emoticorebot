"""Central task execution service backed by focused helper modules."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from emoticorebot.agent.central import backend as central_backend
from emoticorebot.agent.central import prompt as central_prompt
from emoticorebot.agent.central import result as central_result
from emoticorebot.agent.central import stream as central_stream
from emoticorebot.agent.context import ContextBuilder
from emoticorebot.tasks import CentralResultPacket
from emoticorebot.tools import ToolRegistry


class CentralAgentService:
    """Central agent layer for complex tasks."""

    def __init__(
        self,
        central_llm,
        tool_registry: ToolRegistry | None,
        context_builder: ContextBuilder,
    ):
        self.central_llm = central_llm
        self.tools = tool_registry
        self.context = context_builder
        self._agent: Any | None = None
        self._checkpointer: Any | None = None

    async def run_request(
        self,
        request: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
        session_id: str = "",
        task_context: dict[str, Any] | None = None,
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> CentralResultPacket:
        del emotion, pad

        if not central_backend.deep_agents_available():
            return central_result.failed_packet(
                analysis="Deep Agents 依赖尚未安装，central 新执行内核当前不可用。",
                missing=central_result.extract_missing(task_context),
            )

        request = str(request or "").strip()
        resume_value = central_result.build_resume_value(task_context)
        can_resume = (
            resume_value is not None
            and str((task_context or {}).get("thread_id", "") or "").strip() != ""
        )
        if not request and not can_resume:
            return central_result.failed_packet("central 未收到有效问题。")

        if on_progress is not None:
            await on_progress("正在恢复内部执行" if can_resume else "正在规划内部执行")

        agent = central_backend.ensure_agent(self)
        run_id = (
            str((task_context or {}).get("run_id", "") or "").strip()
            if can_resume
            else ""
        )
        if not run_id:
            run_id = central_stream.new_run_id()

        thread_id = (
            str((task_context or {}).get("thread_id", "") or "").strip()
            if can_resume
            else ""
        )
        if not thread_id:
            thread_id = central_stream.build_thread_id(
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
                run_id=run_id,
            )

        prompt = central_prompt.build_request_prompt(
            request=request,
            history=history,
            task_context=task_context,
            media=media,
        )

        try:
            raw_result = await central_stream.invoke_agent(
                self,
                agent,
                prompt,
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
                thread_id=thread_id,
                run_id=run_id,
                on_trace=on_trace,
                resume_value=resume_value if can_resume else None,
            )
        except Exception as exc:
            if can_resume:
                try:
                    raw_result = await central_stream.invoke_agent(
                        self,
                        agent,
                        prompt,
                        channel=channel,
                        chat_id=chat_id,
                        session_id=session_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        on_trace=on_trace,
                        resume_value=None,
                    )
                except Exception as resume_exc:
                    packet = central_result.failed_packet(
                        analysis=f"Deep Agents 恢复失败：{resume_exc}",
                        missing=central_result.extract_missing(task_context),
                    )
                    packet["thread_id"] = thread_id
                    packet["run_id"] = run_id
                    return packet
            else:
                packet = central_result.failed_packet(
                    analysis=f"Deep Agents 执行失败：{exc}",
                    missing=central_result.extract_missing(task_context),
                )
                packet["thread_id"] = thread_id
                packet["run_id"] = run_id
                return packet

        packet = central_result.normalize_result_packet(
            raw_result,
            request=request,
            task_context=task_context,
        )
        packet["thread_id"] = thread_id
        packet["run_id"] = run_id
        return packet


__all__ = ["CentralAgentService"]
