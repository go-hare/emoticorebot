"""Central execution module driven by `agent.system`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from emoticorebot.agent.central import backend as central_backend
from emoticorebot.agent.central import prompt as central_prompt
from emoticorebot.agent.central import result as central_result
from emoticorebot.agent.central import stream as central_stream
from emoticorebot.agent.system import SessionTaskSystem, TaskUnit
from emoticorebot.tools import ToolRegistry

if TYPE_CHECKING:
    from emoticorebot.agent.context import ContextBuilder


@dataclass
class CentralRunState:
    request: str
    history: list[dict[str, Any]] = field(default_factory=list)
    task_context: dict[str, Any] = field(default_factory=dict)
    media: list[str] = field(default_factory=list)
    channel: str = ""
    chat_id: str = ""
    session_id: str = ""
    thread_id: str = ""
    run_id: str = ""
    round_index: int = 0
    latest_summary: str = ""
    latest_question: str = ""
    trace_log: list[dict[str, Any]] = field(default_factory=list)


class CentralAgentService:
    """Central executor with internal per-task state.

    `system.py` owns task lifecycle.
    `central` only runs execution, decides whether to notify staged outcomes,
    and asks the task system for more input when needed.
    """

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
        self._runs: dict[str, CentralRunState] = {}
        self.max_rounds = 4

    async def run_task(self, task: TaskUnit, system: SessionTaskSystem) -> str:
        state = self._runs.get(task.task_id)
        if state is None:
            state = self._create_run_state(task)
            self._runs[task.task_id] = state

        try:
            while True:
                state.round_index += 1
                await self._emit_progress(
                    system,
                    task,
                    message="正在规划内部执行" if state.round_index == 1 else "继续内部执行",
                    event="task.progress",
                    phase="scheduler",
                )

                result = await self._run_single_round(task, state)

                if central_result.should_notify_stage(result, previous=state.latest_summary):
                    await self._emit_progress(
                        system,
                        task,
                        message=result.get("analysis", "") or "",
                        event="task.stage",
                        phase="stage",
                        payload=central_result.build_stage_payload(result),
                    )
                    state.latest_summary = str(result.get("analysis", "") or "").strip()

                if central_result.should_request_input(result):
                    question = central_result.build_input_question(result)
                    field = central_result.pick_input_field(result)
                    state.latest_question = question
                    answer = await system.request_input(task, field=field, question=question)
                    state.history.extend(
                        [
                            {"role": "assistant", "content": question},
                            {"role": "user", "content": answer},
                        ]
                    )
                    state.task_context = central_result.merge_task_context(state.task_context, result)
                    state.task_context["resume_payload"] = {"field": field, "answer": answer}
                    state.request = central_result.build_resume_request(field=field, answer=answer)
                    continue

                if central_result.should_continue(result):
                    state.history.extend(
                        [
                            {"role": "user", "content": state.request},
                            {"role": "assistant", "content": str(result.get("analysis", "") or "").strip()},
                        ]
                    )
                    state.task_context = central_result.merge_task_context(state.task_context, result)
                    if state.round_index >= self.max_rounds:
                        return central_result.build_round_limit_summary(result, rounds=state.round_index)
                    state.request = central_result.build_followup_request(result, previous=state.request)
                    continue

                return str(result.get("analysis", "") or "").strip()
        finally:
            self._runs.pop(task.task_id, None)

    def _create_run_state(self, task: TaskUnit) -> CentralRunState:
        params = dict(task.params or {})
        return CentralRunState(
            request=str(params.get("request", "") or "").strip(),
            history=[dict(item) for item in list(params.get("history", []) or []) if isinstance(item, dict)],
            task_context=dict(params.get("task_context", {}) or {}),
            media=list(params.get("media", []) or []),
            channel=str(params.get("channel", "") or "").strip(),
            chat_id=str(params.get("chat_id", "") or "").strip(),
            session_id=str(params.get("session_id", "") or "").strip(),
        )

    async def _run_single_round(
        self,
        task: TaskUnit,
        state: CentralRunState,
    ) -> central_result.CentralRoundResult:
        if not central_backend.deep_agents_available():
            return central_result.failed_result(
                "Deep Agents 依赖尚未安装，central 当前无法执行内部任务。"
            )

        request = str(state.request or "").strip()
        if not request:
            return central_result.failed_result("central 未收到有效请求。")

        prompt = central_prompt.build_request_prompt(
            request=request,
            history=state.history,
            task_context=state.task_context,
            media=state.media,
        )

        agent = central_backend.ensure_agent(self)
        state.run_id = central_stream.new_run_id()
        if not state.thread_id:
            state.thread_id = central_stream.build_thread_id(
                channel=state.channel,
                chat_id=state.chat_id,
                session_id=state.session_id,
                run_id=task.task_id,
            )

        async def _capture_trace(event: dict[str, Any]) -> None:
            normalized = central_result.normalize_trace_event(event)
            if normalized:
                state.trace_log.append(normalized)

        raw_result = await central_stream.invoke_agent(
            self,
            agent,
            prompt,
            channel=state.channel,
            chat_id=state.chat_id,
            session_id=state.session_id,
            thread_id=state.thread_id,
            run_id=state.run_id,
            on_trace=_capture_trace,
            resume_value=central_result.build_resume_value(state.task_context),
        )

        return central_result.normalize_round_result(
            raw_result,
            task_context=state.task_context,
            thread_id=state.thread_id,
            run_id=state.run_id,
        )

    async def _emit_progress(
        self,
        system: SessionTaskSystem,
        task: TaskUnit,
        *,
        message: str,
        event: str,
        phase: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        text = str(message or "").strip()
        if not text:
            return
        await system.report_progress(
            task,
            text,
            event=event,
            producer="central",
            phase=phase,
            payload=dict(payload or {}),
        )


__all__ = ["CentralAgentService", "CentralRunState"]
