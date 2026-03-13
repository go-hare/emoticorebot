"""Brain service using LangGraph agent with tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.tools import tool

from emoticorebot.agent.context import ContextBuilder
from emoticorebot.runtime.event_bus import RuntimeEventBus
from emoticorebot.types import BrainControlPacket, TaskSpec
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
        
        parts.append("\n\n## 主脑结构化输出要求")
        parts.append("\n系统会强制你输出 `BrainControlPacket` 结构，不要在 `final_message` 中嵌 JSON。")
        parts.append("\n字段语义：")
        parts.append("\n- `intent`: 对用户当前诉求的判断")
        parts.append("\n- `working_hypothesis`: 当前工作假设")
        parts.append("\n- `task_action`: 只能是 `none`、`create_task`、`fill_task`")
        parts.append("\n- `task_reason`: 为什么采取该动作")
        parts.append("\n- `final_decision`: 只能是 `answer`、`ask_user`、`continue`")
        parts.append("\n- `final_message`: 给用户的自然语言回复")
        parts.append("\n- `task_brief`: 当本轮发生任务动作时，给任务系统的简要说明")
        parts.append("\n- `task`: 当且仅当本轮真实调用了 `create_task` 或 `fill_task` 时填写")
        parts.append("\n- `execution_summary`: 一句话总结本轮做了什么；没有执行就填空字符串")
        parts.append("\n规则：")
        parts.append("\n- 直接回复用户：`task_action=none`，`final_decision=answer`。")
        parts.append("\n- 需要追问但不创建任务：`task_action=none`，`final_decision=ask_user`。")
        parts.append("\n- 创建任务前必须先真实调用 `create_task` 工具，然后 `task_action=create_task`，`final_decision=continue`。")
        parts.append("\n- 补充等待任务前必须先真实调用 `fill_task` 工具，然后 `task_action=fill_task`，`final_decision=continue`。")
        parts.append("\n- 不要伪造任务 ID，不要声称创建/补充了并未真实调用的任务。")
        
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
        summary = str(getattr(waiting, "summary", "") or "").strip()
        
        lines = [f"- 任务ID: {waiting.task_id}"]
        if summary:
            lines.append(f"- 当前已完成部分: {summary}")
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
    ) -> BrainControlPacket:
        """Handle user message through the agent and return a structured brain packet."""
        self._task_system = task_system
        
        # 保存当前上下文供工具使用
        self._current_context = {
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
        agent = create_agent(
            model=self.brain_llm,
            tools=tools,
            response_format=ToolStrategy(BrainControlPacket),
        )
        result = await agent.ainvoke({"messages": messages})

        structured = result.get("structured_response")
        return self._normalize_brain_packet(structured)

    def _normalize_brain_packet(self, payload: Any) -> BrainControlPacket:
        """Validate the structured brain packet and enforce tool/action consistency."""
        if not isinstance(payload, dict):
            raise RuntimeError("Brain agent did not return a structured BrainControlPacket")

        packet: BrainControlPacket = {
            "message_id": str(payload.get("message_id", "") or self._current_context.get("message_id", "") or "").strip(),
            "intent": str(payload.get("intent", "") or "").strip(),
            "working_hypothesis": str(payload.get("working_hypothesis", "") or "").strip(),
            "task_action": str(payload.get("task_action", "none") or "none").strip(),
            "task_reason": str(payload.get("task_reason", "") or "").strip(),
            "final_decision": str(payload.get("final_decision", "answer") or "answer").strip(),
            "final_message": str(payload.get("final_message", "") or "").strip(),
            "task_brief": str(payload.get("task_brief", "") or "").strip(),
            "execution_summary": str(payload.get("execution_summary", "") or "").strip(),
            "notify_user": bool(payload.get("notify_user", True)),
            "retrieval_query": str(payload.get("retrieval_query", "") or "").strip(),
            "retrieval_focus": self._normalize_str_list(payload.get("retrieval_focus")),
            "retrieved_memory_ids": self._normalize_str_list(payload.get("retrieved_memory_ids")),
        }

        for key in ("model_name", "prompt_tokens", "completion_tokens", "total_tokens"):
            if key in payload and payload.get(key) not in (None, ""):
                packet[key] = payload.get(key)

        if packet["task_action"] not in {"none", "create_task", "fill_task"}:
            raise RuntimeError(f"Invalid brain task_action: {packet['task_action']!r}")
        if packet["final_decision"] not in {"answer", "ask_user", "continue"}:
            raise RuntimeError(f"Invalid brain final_decision: {packet['final_decision']!r}")
        if not packet["final_message"]:
            raise RuntimeError("BrainControlPacket.final_message must not be empty")

        tool_action = str(self._current_context.get("tool_action", "none") or "none").strip()
        actual_task_spec = self._current_context.get("task_spec")
        if tool_action != "none" and packet["task_action"] != tool_action:
            raise RuntimeError(
                f"BrainControlPacket.task_action={packet['task_action']!r} does not match actual tool action {tool_action!r}"
            )
        if tool_action == "create_task" and packet["final_decision"] != "continue":
            raise RuntimeError("BrainControlPacket.final_decision must be 'continue' after create_task")
        if tool_action == "fill_task" and packet["final_decision"] != "continue":
            raise RuntimeError("BrainControlPacket.final_decision must be 'continue' after fill_task")

        model_task = payload.get("task")
        if actual_task_spec is not None:
            packet["task"] = self._normalize_task_spec(model_task, actual_task_spec)
        elif isinstance(model_task, dict) and model_task:
            packet["task"] = self._normalize_task_spec(model_task)

        if packet["task_action"] in {"create_task", "fill_task"} and "task" not in packet:
            raise RuntimeError("BrainControlPacket.task is required when task_action is create_task or fill_task")

        return packet

    def _normalize_task_spec(self, payload: Any, actual: dict[str, Any] | None = None) -> TaskSpec:
        """Normalize task spec and prefer real runtime-generated task fields."""
        model_task = payload if isinstance(payload, dict) else {}
        source = dict(actual or {})

        def _pick(key: str) -> Any:
            if key in source and source.get(key) not in (None, "", []):
                return source.get(key)
            return model_task.get(key)

        task: TaskSpec = {}
        text_fields = (
            "task_id",
            "origin_message_id",
            "title",
            "request",
            "goal",
            "expected_output",
            "history_context",
            "channel",
            "chat_id",
            "session_id",
        )
        for key in text_fields:
            value = str(_pick(key) or "").strip()
            if value:
                task[key] = value

        list_fields = ("constraints", "success_criteria", "memory_bundle_ids", "skill_hints", "media")
        for key in list_fields:
            values = self._normalize_str_list(_pick(key))
            if values:
                task[key] = values

        history_value = _pick("history")
        if isinstance(history_value, list):
            task["history"] = [dict(item) for item in history_value if isinstance(item, dict)]

        task_context_value = _pick("task_context")
        if isinstance(task_context_value, dict) and task_context_value:
            task["task_context"] = dict(task_context_value)

        if "task_id" not in task:
            raise RuntimeError("BrainControlPacket.task.task_id must not be empty")
        return task

    @staticmethod
    def _normalize_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out
    
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
            flat = "; ".join(
                f"{k}: {v}" for k, v in content.items()
                if v and str(v).strip()
            )
            if flat:
                parts.append(flat)

        return " ".join(parts) if parts else ""

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
    ) -> BrainControlPacket | None:
        """Handle task event through the brain and return a structured brain packet."""
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
            summary = str(event.get("summary", "") or event.get("message", "") or "").strip()
            question = str(event.get("question", "") or "").strip()
            if summary and question:
                content = f"[任务 {task_id} 需要信息] 当前已完成部分：{summary}\n还需要你补充：{question}"
            elif summary:
                content = f"[任务 {task_id} 需要信息] 当前已完成部分：{summary}"
            else:
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

        ev_channel = channel or str(event.get("channel", "") or "").strip()
        ev_chat_id = chat_id or str(event.get("chat_id", "") or "").strip()
        event_message_id = str(event.get("message_id", "") or "").strip()
        self._current_context = {
            "history": history,
            "media": [],
            "message_id": event_message_id,
            "channel": ev_channel,
            "chat_id": ev_chat_id,
            "session_id": session_id,
            "tool_action": "none",
            "task_spec": None,
        }
        
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
                llm_content = blocks_to_llm_content(turn_content)
                if llm_content:
                    messages.append({"role": role, "content": llm_content})
        messages.append({"role": "user", "content": content})

        tools = self._build_tools(
            channel=ev_channel, chat_id=ev_chat_id, session_id=session_id,
        )
        agent = create_agent(
            model=self.brain_llm,
            tools=tools,
            response_format=ToolStrategy(BrainControlPacket),
        )
        result = await agent.ainvoke({"messages": messages})
        structured = result.get("structured_response")
        return self._normalize_brain_packet(structured)


__all__ = ["BrainService"]
