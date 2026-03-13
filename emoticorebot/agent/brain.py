"""Brain service using LangGraph agent with tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from langchain_core.tools import tool
from langgraph.prebuilt import create_agent

from emoticorebot.agent.context import ContextBuilder
from emoticorebot.runtime.event_bus import RuntimeEventBus
from emoticorebot.utils.llm_utils import blocks_to_llm_content

if TYPE_CHECKING:
    from emoticorebot.agent.system import SessionTaskSystem


class BrainService:
    """Brain agent with task management tools."""

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
        self._task_system: SessionTaskSystem | None = None
        self._current_context: dict[str, Any] = {}

    def _build_tools(self, channel: str = "", chat_id: str = "", session_id: str = ""):
        """Build tools that have access to task_system and context."""
        task_system = self._task_system
        current_context = self._current_context

        @tool
        async def create_task(task_description: str, task_title: str = "", history_context: str = "") -> str:
            """创建一个复杂任务，委托给 central 执行。
            
            当用户请求需要多步骤处理、工具调用、信息查询等复杂操作时使用。
            
            Args:
                task_description: 任务描述，说明需要完成什么
                task_title: 任务标题，简短描述任务（如"查天气"、"搜索资料"）
                history_context: 相关的历史上下文（可选）
            """
            if task_system is None:
                return "任务系统未初始化"
            
            task_id = f"task_{uuid4().hex[:12]}"
            title = str(task_title or "").strip() or task_description[:20]
            
            # 从当前上下文获取完整的会话信息
            history = current_context.get("history", [])
            media = current_context.get("media", [])
            message_id = current_context.get("message_id", "")
            
            await task_system.create_central_task(
                task_id,
                title=title,
                request=task_description,
                history=history,
                task_context={"history_context": history_context} if history_context else {},
                media=media,
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
                extra_params={"message_id": message_id} if message_id else {},
            )
            return f"已创建任务「{title}」({task_id})，正在处理中"

        @tool
        async def fill_task(answer: str, task_id: str = "") -> str:
            """补充当前等待任务所需的信息。
            
            当有任务正在等待用户提供额外信息时，使用此工具提交用户的回答。
            
            Args:
                answer: 用户提供的信息/回答
                task_id: 任务ID（可选，默认补充当前等待的任务）
            """
            if task_system is None:
                return "任务系统未初始化"
            
            waiting = task_system.waiting_task()
            if waiting is None:
                return "当前没有等待信息的任务"
            
            target_id = task_id or waiting.task_id
            success = await task_system.answer(answer, target_id)
            if success:
                return f"已提交信息到任务 {target_id}，继续处理中"
            return "提交信息失败"

        @tool
        async def cancel_task(task_id: str = "") -> str:
            """取消一个任务。
            
            当用户明确表示不想继续某个任务时调用。
            
            Args:
                task_id: 要取消的任务ID（可选，默认取消当前等待的任务）
            """
            if task_system is None:
                return "任务系统未初始化"
            
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
            """查询任务状态。
            
            当用户询问任务进度、状态时使用。
            不传参数则返回所有执行中任务的信息。
            
            Args:
                task_id: 任务ID（可选）
                task_title: 任务标题（可选，支持模糊匹配）
            """
            if task_system is None:
                return "任务系统未初始化"
            
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
        """Build system prompt with current state."""
        base = self.context.build_brain_system_prompt(
            query=user_query,  # 使用当前用户输入作为记忆检索查询
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
        
        # 添加 JSON 输出格式要求
        parts.append("\n\n## 输出格式要求")
        parts.append("\n你的回复必须是 JSON 格式：")
        parts.append("\n```json")
        parts.append('\n{')
        parts.append('\n  "message": "给用户的回复内容",')
        parts.append('\n  "execution_summary": "简要说明本轮做了什么（调用了什么工具、目的是什么、预期结果）"')
        parts.append('\n}')
        parts.append("\n```")
        parts.append("\n**注意**：")
        parts.append("\n- message: 用自然语言回复用户")
        parts.append("\n- execution_summary: 一句话总结你的操作，如果没有调用工具则填空字符串")
        
        return "".join(parts)

    def _get_waiting_task_info(self) -> str:
        """Get waiting task info for system prompt."""
        if self._task_system is None:
            return ""
        
        waiting = self._task_system.waiting_task()
        if waiting is None:
            return ""
        
        input_request = getattr(waiting, "input_request", None) or {}
        missing = list(getattr(waiting, "missing", []) or [])
        question = str(input_request.get("question", "") or "")
        
        lines = [f"- 任务ID: {waiting.task_id}"]
        if missing:
            lines.append(f"- 缺少信息: {missing}")
        if question:
            lines.append(f"- 追问内容: {question}")
        
        return "\n".join(lines)

    async def handle_user_message(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        internal_history: list[dict[str, Any]] | None = None,
        emotion: str,
        pad: dict[str, float],
        task_system: "SessionTaskSystem | None" = None,
        message_id: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
        media: list[str] | None = None,
    ) -> dict[str, Any]:
        """Handle user message through the agent. Returns message and execution summary."""
        self._task_system = task_system
        
        # 保存当前上下文供工具使用
        self._current_context = {
            "history": history,
            "media": media or [],
            "message_id": message_id,
            "channel": channel,
            "chat_id": chat_id,
            "session_id": session_id,
        }
        
        system_prompt = self._build_state_modifier(
            emotion=emotion,
            pad=pad,
            waiting_task_info=self._get_waiting_task_info(),
            user_query=user_input,
        )
        
        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        
        internal = internal_history or []
        for turn in internal[-10:]:
            role = turn.get("role", "user")
            if role not in ("user", "assistant"):
                continue
            content = turn.get("content", "")
            if not content:
                continue
            if isinstance(content, dict):
                text = self._serialize_internal_content(turn)
            else:
                text = str(content)
            if text:
                messages.append({"role": role, "content": text})
        
        # Add dialogue history (preserve multimodal content)
        for turn in history[-20:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                llm_content = blocks_to_llm_content(content)
                if llm_content:
                    messages.append({"role": role, "content": llm_content})
        
        # Add current user input with multimodal media if available
        media_items = self.context.build_media_context(media)
        if media_items:
            user_content: list[dict[str, Any]] = [
                {"type": "text", "text": user_input},
                *media_items,
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_input})
        
        # Create agent with current context
        tools = self._build_tools(channel=channel, chat_id=chat_id, session_id=session_id)
        agent = create_agent(model=self.brain_llm, tools=tools)
        result = await agent.ainvoke({"messages": messages})
        
        response_messages = result.get("messages", [])
        raw_message = response_messages[-1].content if response_messages else ""
        
        # Parse JSON response
        parsed = self._parse_brain_response(raw_message)
        
        return {
            "message": parsed.get("message", raw_message),
            "execution_summary": parsed.get("execution_summary", ""),
        }
    
    @staticmethod
    def _serialize_internal_content(record: dict[str, Any]) -> str:
        """Convert structured internal history record to readable text for LLM context."""
        content = record.get("content", {})
        if not isinstance(content, dict):
            return str(content) if content else ""

        event = str(record.get("event", "") or "").strip()
        phase = str(record.get("phase", "") or "").strip()

        parts: list[str] = []
        if phase:
            parts.append(f"[{phase}]")

        if event == "brain.decision":
            decision = str(content.get("decision", "") or "").strip()
            reasoning = str(content.get("reasoning", "") or "").strip()
            if decision:
                parts.append(f"决策: {decision}")
            if reasoning:
                parts.append(f"原因: {reasoning}")
        elif event == "task.executed":
            status = str(content.get("status", "") or "").strip()
            summary = str(content.get("summary", "") or "").strip()
            if status:
                parts.append(f"任务状态: {status}")
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
            flat = "; ".join(
                f"{k}: {v}" for k, v in content.items()
                if v and str(v).strip()
            )
            if flat:
                parts.append(flat)

        return " ".join(parts) if parts else ""

    def _parse_brain_response(self, raw_message: str) -> dict[str, Any]:
        """Parse JSON response from Brain."""
        import json
        import re
        
        # Try to extract JSON from message
        # Look for ```json ... ``` or { ... }
        json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', raw_message)
        if not json_match:
            json_match = re.search(r'(\{[\s\S]*?\})', raw_message)
        
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        
        # Fallback: treat entire message as user message
        return {
            "message": raw_message,
            "execution_summary": "",
        }

    async def handle_task_event(
        self,
        *,
        event: dict[str, Any],
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        task_system: "SessionTaskSystem | None" = None,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> str | None:
        """Handle task event (from Central) through the agent. Returns response text or None if ignored."""
        if task_system is not None:
            self._task_system = task_system

        event_type = str(event.get("type", "") or "").strip()
        task_id = str(event.get("task_id", "") or "").strip()
        
        # Format event as natural language input
        if event_type == "done":
            summary = str(event.get("summary", "") or "").strip()
            content = f"[任务 {task_id} 完成] {summary or '处理完成'}"
        elif event_type == "failed":
            reason = str(event.get("reason", "") or "").strip()
            content = f"[任务 {task_id} 失败] {reason or '执行出错'}"
        elif event_type == "need_input":
            question = str(event.get("question", "") or "").strip()
            content = f"[任务 {task_id} 需要信息] {question or '需要更多信息才能继续'}"
        elif event_type == "progress":
            message = str(event.get("message", "") or "").strip()
            payload = event.get("payload", {}) or {}
            phase = str(payload.get("phase", "") or "").strip()
            if phase == "stage" and message:
                content = f"[任务 {task_id} 进度] {message}"
            else:
                return None  # Ignore non-stage progress events
        else:
            return None  # Ignore unknown events
        
        system_prompt = self._build_state_modifier(
            emotion=emotion,
            pad=pad,
            waiting_task_info=self._get_waiting_task_info(),
            user_query=content,
        )
        
        messages = [{"role": "system", "content": system_prompt}]
        for turn in history[-10:]:
            role = turn.get("role", "user")
            turn_content = turn.get("content", "")
            if role in ("user", "assistant") and turn_content:
                messages.append({"role": role, "content": str(turn_content)})
        messages.append({"role": "user", "content": content})
        
        ev_channel = channel or str(event.get("channel", "") or "").strip()
        ev_chat_id = chat_id or str(event.get("chat_id", "") or "").strip()
        tools = self._build_tools(
            channel=ev_channel, chat_id=ev_chat_id, session_id=session_id,
        )
        agent = create_agent(model=self.brain_llm, tools=tools)
        result = await agent.ainvoke({"messages": messages})
        response_messages = result.get("messages", [])
        if response_messages:
            raw = response_messages[-1].content
            parsed = self._parse_brain_response(raw)
            return parsed.get("message", raw)
        return ""


__all__ = ["BrainService"]
