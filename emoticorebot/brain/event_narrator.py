"""Transforms runtime task events into companion-facing narration."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from emoticorebot.brain.companion_brain import CompanionBrain
from emoticorebot.brain.decision_packet import BrainControlPacket, normalize_brain_packet
from emoticorebot.protocol.events import TaskEvent
from emoticorebot.runtime.session_runtime import SessionRuntime
from emoticorebot.utils.llm_utils import blocks_to_llm_content


class EventNarrator(CompanionBrain):
    """Handles user-facing narration of runtime task events."""

    def _build_narration_prompt(
        self,
        *,
        emotion: str,
        pad: dict[str, float],
        waiting_task_info: str,
        event_input: str,
    ) -> str:
        base = self.context.build_brain_system_prompt(
            query=event_input,
            current_emotion=emotion,
            pad_state=(
                pad.get("pleasure", 0.0),
                pad.get("arousal", 0.5),
                pad.get("dominance", 0.5),
            ),
        )
        parts = [base]
        if waiting_task_info:
            parts.append(f"\n\n## 当前等待用户补充信息的任务\n{waiting_task_info}")
        parts.append("\n\n## 任务事件转述规则")
        parts.append("\n你收到的是 runtime 任务事件，不是新的用户请求。")
        parts.append("\n你的职责是判断这条事件是否值得告诉用户，以及用陪伴式语言怎么说。")
        parts.append("\n- `task_action` 必须是 `none`。")
        parts.append("\n- 不要创建任务，不要补充任务，不要取消任务。")
        parts.append("\n- `final_decision` 只能是 `answer` 或 `ask_user`。")
        parts.append("\n- `final_message` 必须是给用户看的自然语言，不要复述内部字段名。")
        parts.append("\n- `execution_summary` 用一句话总结这条任务事件。")
        parts.append("\n- 如果这是等待用户补充信息的事件，要把追问自然地说给用户。")
        parts.append("\n- 如果只是低价值进度噪音，就返回空消息是不允许的；应该在进入这里前就被过滤。")
        return "".join(parts)

    @staticmethod
    def _format_event_content(event: TaskEvent) -> str | None:
        event_type = str(event.get("type", "") or "").strip()
        task_id = str(event.get("task_id", "") or "").strip()

        if event_type == "done":
            summary = str(event.get("summary", "") or "").strip()
            return f"[任务 {task_id} 完成] {summary or '处理完成'}"
        if event_type == "failed":
            reason = str(event.get("reason", "") or "").strip()
            return f"[任务 {task_id} 失败] {reason or '执行出错'}"
        if event_type == "need_input":
            summary = str(event.get("summary", "") or event.get("message", "") or "").strip()
            question = str(event.get("question", "") or "").strip()
            if summary and question:
                return f"[任务 {task_id} 需要信息] 当前已完成部分：{summary}\n还需要你补充：{question}"
            if summary:
                return f"[任务 {task_id} 需要信息] 当前已完成部分：{summary}"
            return f"[任务 {task_id} 需要信息] {question or '需要更多信息才能继续'}"
        if event_type == "progress":
            message = str(event.get("message", "") or "").strip()
            payload = event.get("payload", {}) or {}
            phase = str(payload.get("phase", "") or "").strip()
            if phase == "stage" and message:
                return f"[任务 {task_id} 进度] {message}"
        return None

    async def handle_task_event(
        self,
        *,
        event: TaskEvent,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        task_system: SessionRuntime | None = None,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainControlPacket | None:
        content = self._format_event_content(event)
        if not content:
            return None

        ev_channel = channel or str(event.get("channel", "") or "").strip()
        ev_chat_id = chat_id or str(event.get("chat_id", "") or "").strip()
        current_context: dict[str, Any] = {
            "history": history,
            "media": [],
            "message_id": str(event.get("message_id", "") or "").strip(),
            "channel": ev_channel,
            "chat_id": ev_chat_id,
            "session_id": session_id,
            "tool_action": "none",
            "task_spec": None,
        }

        system_prompt = self._build_narration_prompt(
            emotion=emotion,
            pad=pad,
            waiting_task_info=self._get_waiting_task_info(task_system),
            event_input=content,
        )
        messages = [{"role": "system", "content": system_prompt}]
        for turn in history[-10:]:
            role = turn.get("role", "user")
            turn_content = turn.get("content", "")
            if role in ("user", "assistant") and turn_content:
                llm_content = blocks_to_llm_content(turn_content)
                if llm_content:
                    messages.append({"role": role, "content": llm_content})
        messages.append({"role": "user", "content": content})

        agent = create_agent(
            model=self.brain_llm,
            tools=[],
            response_format=ToolStrategy(BrainControlPacket),
        )
        result = await agent.ainvoke({"messages": messages})
        structured = result.get("structured_response")
        return normalize_brain_packet(structured, current_context=current_context)


__all__ = ["EventNarrator"]
