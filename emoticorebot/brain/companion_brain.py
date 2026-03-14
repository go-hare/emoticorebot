"""Companion-facing decision brain."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool

from emoticorebot.agent.context import ContextBuilder
from emoticorebot.brain.decision_packet import BrainControlPacket, normalize_brain_packet
from emoticorebot.protocol.task_models import TaskSpec
from emoticorebot.runtime.event_bus import RuntimeEventBus
from emoticorebot.runtime.session_runtime import SessionRuntime
from emoticorebot.utils.llm_utils import blocks_to_llm_content, extract_message_text


class CompanionBrain:
    """Handles user-turn decisions in the companion layer."""

    def __init__(
        self,
        brain_llm,
        context_builder: ContextBuilder,
        *,
        bus: RuntimeEventBus | None = None,
    ):
        self.brain_llm = brain_llm
        self.context = context_builder
        self.bus = bus

    def _build_tools(
        self,
        *,
        task_system: SessionRuntime | None,
        current_context: dict[str, Any],
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ):
        """Build tools that have access to the current runtime and turn context."""

        @tool
        async def create_task(task_description: str, task_title: str = "", history_context: str = "") -> str:
            """Create a session-scoped task for the central executor to run asynchronously."""
            if task_system is None:
                return "SessionRuntime 未初始化"

            task_id = f"task_{uuid4().hex[:12]}"
            title = str(task_title or "").strip() or task_description[:20]

            history = current_context.get("history", [])
            media = current_context.get("media", [])
            message_id = current_context.get("message_id", "")
            task_spec: TaskSpec = {
                "task_id": task_id,
                "origin_message_id": str(message_id or "").strip(),
                "title": title,
                "request": str(task_description or "").strip(),
                "history": [dict(item) for item in list(history or []) if isinstance(item, dict)],
                "task_context": {"history_context": history_context} if history_context else {},
                "history_context": str(history_context or "").strip(),
                "media": [str(item).strip() for item in list(media or []) if str(item).strip()],
                "channel": str(channel or "").strip(),
                "chat_id": str(chat_id or "").strip(),
                "session_id": str(session_id or "").strip(),
            }

            await task_system.create_central_task(task_spec)
            current_context["tool_action"] = "create_task"
            current_context["task_spec"] = dict(task_spec)
            return f"已创建任务「{title}」({task_id})，正在处理中"

        @tool
        async def fill_task(answer: str, task_id: str = "") -> str:
            """Provide follow-up user input to a waiting session task and resume execution."""
            if task_system is None:
                return "SessionRuntime 未初始化"

            waiting = task_system.waiting_task()
            if waiting is None:
                return "当前没有等待信息的任务"

            target_id = task_id or waiting.task_id
            success = await task_system.answer(
                answer,
                target_id,
                origin_message_id=str(current_context.get("message_id", "") or "").strip(),
            )
            if success:
                current_context["tool_action"] = "fill_task"
                current_context["task_spec"] = {
                    "task_id": str(target_id or "").strip(),
                    "origin_message_id": str(current_context.get("message_id", "") or "").strip(),
                    "title": str(getattr(waiting, "title", "") or "").strip(),
                    "request": str(answer or "").strip(),
                    "channel": str(channel or "").strip(),
                    "chat_id": str(chat_id or "").strip(),
                    "session_id": str(session_id or "").strip(),
                }
                return f"已提交信息到任务 {target_id}，继续处理中"
            return "提交信息失败"

        @tool
        async def cancel_task(task_id: str = "") -> str:
            """Cancel a live session task that is currently waiting or still running."""
            if task_system is None:
                return "SessionRuntime 未初始化"

            waiting = task_system.waiting_task()
            if waiting is None and not task_id:
                return "当前没有可取消的任务"

            target = task_system.get_task(task_id) if task_id else waiting
            if target is None:
                return f"找不到任务 {task_id}"

            await task_system.fail_task(target, reason="用户取消")
            return f"已取消任务 {target.task_id}"

        @tool
        async def query_task(task_id: str = "", task_title: str = "") -> str:
            """Query the current state of live session tasks by id, title, or list all active tasks."""
            if task_system is None:
                return "SessionRuntime 未初始化"

            if task_id:
                task = task_system.get_task(task_id)
                if task is None:
                    return f"找不到任务 {task_id}"
                status_text = {
                    "running": "执行中",
                    "waiting_input": "等待补充信息",
                    "blocked_input": "排队等待",
                    "done": "已完成",
                    "failed": "失败",
                }.get(task.status, task.status)
                result = f"任务「{task.title or task.task_id}」: {status_text}"
                if task.stage_info:
                    result += f"\n当前进度: {task.stage_info}"
                return result

            if task_title:
                task = task_system.find_task_by_title(task_title)
                if task is None:
                    return f"找不到标题包含「{task_title}」的任务"
                status_text = {
                    "running": "执行中",
                    "waiting_input": "等待补充信息",
                    "blocked_input": "排队等待",
                    "done": "已完成",
                    "failed": "失败",
                }.get(task.status, task.status)
                result = f"任务「{task.title or task.task_id}」: {status_text}"
                if task.stage_info:
                    result += f"\n当前进度: {task.stage_info}"
                return result

            return task_system.get_tasks_summary()

        return [create_task, fill_task, cancel_task, query_task]

    def _build_state_modifier(
        self,
        *,
        emotion: str,
        pad: dict[str, float],
        waiting_task_info: str = "",
        user_query: str = "",
    ) -> str:
        base = self.context.build_brain_system_prompt(
            query=user_query,
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
            parts.append("\n如果用户的回复是在补充上述信息，请调用 fill_task 工具。")
            parts.append("如果用户说不想继续或取消，请调用 cancel_task 工具。")
            parts.append("如果用户在说其他事情，正常回复即可。")

        parts.append("\n\n## 主脑结构化输出要求")
        parts.append("\n系统会强制你输出 `BrainControlPacket` 结构，不要在 `final_message` 中嵌 JSON。")
        parts.append("\n字段语义：")
        parts.append("\n- `intent`: 对用户当前诉求的判断")
        parts.append("\n- `working_hypothesis`: 当前工作假设")
        parts.append("\n- `task_action`: 只能是 `none`、`create_task`、`fill_task`")
        parts.append("\n- `task_reason`: 为什么采取该动作")
        parts.append("\n- `final_decision`: 只能是 `answer`、`ask_user`、`continue`")
        parts.append("\n- `final_message`: 给用户的自然语言回复")
        parts.append("\n- `task_brief`: 当本轮发生任务动作时，给 SessionRuntime 的简要说明")
        parts.append("\n- `task`: 当且仅当本轮真实调用了 `create_task` 或 `fill_task` 时填写")
        parts.append("\n- `execution_summary`: 一句话总结本轮做了什么；没有执行就填空字符串")
        parts.append("\n规则：")
        parts.append("\n- 直接回复用户：`task_action=none`，`final_decision=answer`。")
        parts.append("\n- 需要追问但不创建任务：`task_action=none`，`final_decision=ask_user`。")
        parts.append("\n- 创建任务前必须先真实调用 `create_task` 工具，然后 `task_action=create_task`，`final_decision=continue`。")
        parts.append("\n- 补充等待任务前必须先真实调用 `fill_task` 工具，然后 `task_action=fill_task`，`final_decision=continue`。")
        parts.append("\n- 不要伪造任务 ID，不要声称创建/补充了并未真实调用的任务。")

        return "".join(parts)

    @staticmethod
    def _get_waiting_task_info(task_system: SessionRuntime | None) -> str:
        if task_system is None:
            return ""

        waiting = task_system.waiting_task()
        if waiting is None:
            return ""

        input_request = getattr(waiting, "input_request", None) or {}
        missing = list(getattr(waiting, "missing", []) or [])
        question = str(input_request.get("question", "") or "")
        summary = str(getattr(waiting, "summary", "") or "").strip()

        lines = [f"- 任务ID: {waiting.task_id}"]
        if summary:
            lines.append(f"- 当前已完成部分: {summary}")
        if missing:
            lines.append(f"- 缺少信息: {missing}")
        if question:
            lines.append(f"- 追问内容: {question}")
        return "\n".join(lines)

    @staticmethod
    def _serialize_internal_content(record: dict[str, Any]) -> str:
        content = record.get("content", {})
        if not isinstance(content, dict):
            return str(content) if content else ""

        event = str(record.get("event", "") or "").strip()
        phase = str(record.get("phase", "") or "").strip()

        parts: list[str] = []
        if phase:
            parts.append(f"[{phase}]")

        if event == "brain.decision":
            intent = str(content.get("intent", "") or "").strip()
            hypothesis = str(content.get("working_hypothesis", "") or "").strip()
            task_action = str(content.get("task_action", "") or "").strip()
            task_reason = str(content.get("task_reason", "") or "").strip()
            final_decision = str(content.get("final_decision", "") or "").strip()
            task_brief = str(content.get("task_brief", "") or "").strip()
            execution_summary = str(content.get("execution_summary", "") or "").strip()
            if intent:
                parts.append(f"意图: {intent}")
            if hypothesis:
                parts.append(f"假设: {hypothesis}")
            if task_action:
                parts.append(f"任务动作: {task_action}")
            if task_reason:
                parts.append(f"动作原因: {task_reason}")
            if final_decision:
                parts.append(f"最终决策: {final_decision}")
            if task_brief:
                parts.append(f"任务摘要: {task_brief}")
            if execution_summary:
                parts.append(f"执行摘要: {execution_summary}")
        elif event == "task.executed":
            status = str(content.get("status", "") or "").strip()
            result_status = str(content.get("result_status", "") or "").strip()
            summary = str(content.get("summary", "") or "").strip()
            if status:
                parts.append(f"任务状态: {status}")
            if result_status:
                parts.append(f"结果状态: {result_status}")
            if summary:
                parts.append(summary)
        elif event == "execution.trace":
            trace_summary = str(content.get("trace_summary", "") or "").strip()
            if trace_summary:
                parts.append(trace_summary)
        elif event == "brain.turn.summary":
            output = str(content.get("output", "") or "").strip()
            if output:
                parts.append(output[:200])
        else:
            flat = "; ".join(f"{k}: {v}" for k, v in content.items() if v and str(v).strip())
            if flat:
                parts.append(flat)

        return " ".join(parts) if parts else ""

    async def handle_user_message(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        internal_history: list[dict[str, Any]] | None = None,
        emotion: str,
        pad: dict[str, float],
        task_system: SessionRuntime | None = None,
        message_id: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
        media: list[str] | None = None,
    ) -> BrainControlPacket:
        current_context: dict[str, Any] = {
            "history": history,
            "media": media or [],
            "message_id": message_id,
            "channel": channel,
            "chat_id": chat_id,
            "session_id": session_id,
            "tool_action": "none",
            "task_spec": None,
        }

        system_prompt = self._build_state_modifier(
            emotion=emotion,
            pad=pad,
            waiting_task_info=self._get_waiting_task_info(task_system),
            user_query=user_input,
        )

        messages = [{"role": "system", "content": system_prompt}]

        internal = internal_history or []
        for turn in internal[-10:]:
            role = turn.get("role", "user")
            if role not in ("user", "assistant"):
                continue
            content = turn.get("content", "")
            if not content:
                continue
            text = self._serialize_internal_content(turn) if isinstance(content, dict) else str(content)
            if text:
                messages.append({"role": role, "content": text})

        for turn in history[-20:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                llm_content = blocks_to_llm_content(content)
                if llm_content:
                    messages.append({"role": role, "content": llm_content})

        media_items = self.context.build_media_context(media)
        if media_items:
            user_content: list[dict[str, Any]] = [{"type": "text", "text": user_input}, *media_items]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_input})

        tools = self._build_tools(
            task_system=task_system,
            current_context=current_context,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )
        agent = create_agent(
            model=self.brain_llm,
            tools=tools,
            response_format=ToolStrategy(BrainControlPacket),
        )
        result = await agent.ainvoke({"messages": messages})
        structured = result.get("structured_response")
        return normalize_brain_packet(structured, current_context=current_context)

    async def generate_proactive(
        self,
        prompt: str,
        *,
        emotion: str = "平静",
        pad: dict[str, float] | None = None,
    ) -> str:
        """Generate a proactive companion message without task tools."""
        pad_state = pad or {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
        system_prompt = self.context.build_brain_system_prompt(
            query=prompt,
            current_emotion=emotion,
            pad_state=(
                pad_state.get("pleasure", 0.0),
                pad_state.get("arousal", 0.5),
                pad_state.get("dominance", 0.5),
            ),
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        if hasattr(self.brain_llm, "ainvoke"):
            result = await self.brain_llm.ainvoke(messages)
        elif hasattr(self.brain_llm, "invoke"):
            result = self.brain_llm.invoke(messages)
        else:
            raise RuntimeError("brain model does not expose invoke/ainvoke")
        return extract_message_text(result)


__all__ = ["CompanionBrain"]
