"""Transforms runtime task events into companion-facing narration."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from emoticorebot.brain.companion_brain import CompanionBrain
from emoticorebot.brain.decision_packet import BrainControlPacket, normalize_brain_packet, parse_raw_brain_json
from emoticorebot.protocol.events import TaskEvent
from emoticorebot.runtime.session_runtime import SessionRuntime
from emoticorebot.utils.llm_utils import blocks_to_llm_content


class EventNarrator(CompanionBrain):
    """Handles user-facing narration of runtime task events."""

    @staticmethod
    def _should_suppress_progress_message(message: str) -> bool:
        text = str(message or "").strip()
        if not text:
            return True

        low_value_markers = {
            "正在执行内部任务",
            "处理中",
            "继续处理中",
        }
        if text in low_value_markers:
            return True

        user_input_markers = (
            "?",
            "？",
            "请提供",
            "请补充",
            "补充信息",
            "需要你",
            "需要您",
            "告诉我",
            "告诉一下",
            "哪个城市",
            "什么城市",
            "你在哪",
            "您在哪",
        )
        return any(marker in text for marker in user_input_markers)

    @staticmethod
    def _build_direct_event_packet(event: TaskEvent) -> BrainControlPacket | None:
        event_type = str(event.get("type", "") or "").strip().lower()
        if event_type not in {"done", "failed", "need_input"}:
            return None

        task_id = str(event.get("task_id", "") or "").strip()
        title = str(event.get("title", "") or "").strip() or task_id or "任务"
        message_id = str(event.get("message_id", "") or "").strip()

        if event_type == "done":
            summary = str(event.get("summary", "") or "").strip() or "处理完成。"
            final_message = f"「{title}」已经完成。{summary}"
            final_decision = "answer"
            intent = "task_done"
            working_hypothesis = "任务已经完成，直接通知用户结果。"
            execution_summary = "通知用户任务完成。"
        elif event_type == "failed":
            reason = str(event.get("reason", "") or "").strip() or "暂时没有更多错误信息。"
            final_message = f"「{title}」执行失败了。{reason}"
            final_decision = "answer"
            intent = "task_failed"
            working_hypothesis = "任务执行失败，需要立即告知用户。"
            execution_summary = "通知用户任务失败。"
        else:
            summary = str(event.get("summary", "") or event.get("message", "") or "").strip()
            question = str(event.get("question", "") or "").strip()
            if summary and question:
                final_message = f"「{title}」已经完成一部分：{summary}。还需要你补充：{question}"
            elif question:
                final_message = f"「{title}」还需要你补充信息：{question}"
            elif summary:
                final_message = f"「{title}」已经完成一部分：{summary}。还需要你补充一点信息才能继续。"
            else:
                final_message = f"「{title}」还需要你补充一点信息才能继续。"
            final_decision = "ask_user"
            intent = "task_need_input"
            working_hypothesis = "任务需要用户补充信息，直接追问即可。"
            execution_summary = "向用户追问任务所需信息。"

        return {
            "message_id": message_id,
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "task_action": "none",
            "task_reason": "这是任务事件通知，不需要再创建或修改任务。",
            "final_decision": final_decision,
            "final_message": final_message,
            "task_brief": "",
            "execution_summary": execution_summary,
            "notify_user": True,
            "retrieval_query": "",
            "retrieval_focus": [],
            "retrieved_memory_ids": [],
        }

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

        parts.append("\n\n## 结构化输出要求")
        parts.append("\n你必须且只能输出一个合法的 JSON 对象（不要包裹在 markdown 代码块中），")
        parts.append("严格遵循以下 `BrainControlPacket` schema：")
        parts.append('\n```json\n{\n'
                     '  "intent": "<string: 对本次事件的判断>",\n'
                     '  "working_hypothesis": "<string: 当前工作假设>",\n'
                     '  "task_action": "none",\n'
                     '  "task_reason": "<string: 为什么采取该动作>",\n'
                     '  "final_decision": "<enum: answer | ask_user>",\n'
                     '  "final_message": "<string: 给用户的自然语言回复>",\n'
                     '  "task_brief": "",\n'
                     '  "execution_summary": "<string: 一句话总结这条任务事件>"\n'
                     '}\n```')
        parts.append("\n⚠️ 重要约束：")
        parts.append("\n- 输出必须是可被 `json.loads()` 直接解析的纯 JSON，不要输出任何 JSON 之外的文字。")
        parts.append("\n- `task_action` 必须是 `none`，不要创建/补充/取消任务。")
        parts.append("\n- `final_decision` 只能是 `answer` 或 `ask_user`。")
        parts.append("\n- `final_message` 必须是给用户看的自然语言，不要复述内部字段名。")
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
            if phase == "stage" and message and not EventNarrator._should_suppress_progress_message(message):
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

        direct_packet = self._build_direct_event_packet(event)
        if direct_packet is not None:
            return direct_packet

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
        )
        result = await agent.ainvoke({"messages": messages})
        structured = parse_raw_brain_json(result)
        return normalize_brain_packet(structured, current_context=current_context)


__all__ = ["EventNarrator"]
