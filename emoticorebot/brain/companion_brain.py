"""Companion-facing decision brain."""

from __future__ import annotations

import re
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

    _TASK_VERB_PATTERN = re.compile(
        r"(这是一个复杂任务|写|创建|新建|生成|修改|更新|编辑|新增|添加|删除|重构|修复|运行|执行|测试|安装|查找|搜索|"
        r"create|write|modify|update|edit|add|delete|refactor|fix|run|test|install|search)",
        re.IGNORECASE,
    )
    _CODE_SIGNAL_PATTERN = re.compile(
        r"(```|`[^`]+`|"
        r"\bdef\b|\bclass\b|\bfunction\b|\breturn\b|"
        r"\bpytest\b|\bpip\b|\bnpm\b|\buv\b|\bpython\b|"
        r"[A-Za-z0-9_\-./\\\\]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md|txt|toml|ini|cfg|sh|ps1))",
        re.IGNORECASE,
    )
    _STATUS_QUERY_PATTERN = re.compile(
        r"(创建好了吗|创建完了吗|好了吗|好了没|完成了吗|完成没|做完了吗|进度|结果呢|怎么样了|还在吗|status|progress|done|finished)",
        re.IGNORECASE,
    )
    _GREETING_PATTERN = re.compile(
        r"^(你好|您好|嗨|hi|hello|哈喽|在吗|在不在|早上好|中午好|晚上好)[!！。\.~～\s]*$",
        re.IGNORECASE,
    )
    _WAITING_TASK_CANCEL_PATTERN = re.compile(
        r"(取消|不用了|先不用|算了|停下|停止|stop|cancel)",
        re.IGNORECASE,
    )
    _WAITING_TASK_QUESTION_PATTERN = re.compile(
        r"(\?|？|什么|为啥|为什么|怎么|咋|如何|哪里|哪儿|哪个|谁|吗|呢|么|是否|能不能|可不可以|"
        r"帮我|请|查一下|看一下|告诉我|what|why|how|when|where|who)",
        re.IGNORECASE,
    )
    _FILE_REF_PATTERN = re.compile(
        r"([A-Za-z0-9_\-./\\\\]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md|txt|toml|ini|cfg|sh|ps1))",
        re.IGNORECASE,
    )

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

            task_spec = await self._create_session_task(
                task_system=task_system,
                current_context=current_context,
                request=task_description,
                title=task_title,
                history_context=history_context,
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
            )
            task_id = str(task_spec.get("task_id", "") or "").strip()
            resolved_title = str(task_spec.get("title", "") or "").strip()
            return f"已创建任务「{resolved_title}」({task_id})，正在处理中"

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

    @staticmethod
    def _derive_task_title(request: str, explicit_title: str = "") -> str:
        title = str(explicit_title or "").strip()
        if title:
            return title
        text = str(request or "").strip()
        if not text:
            return "执行任务"
        file_match = re.search(
            r"([A-Za-z0-9_\-./\\\\]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md|txt|toml|ini|cfg|sh|ps1))",
            text,
            re.IGNORECASE,
        )
        file_name = file_match.group(1) if file_match else ""
        action = ""
        if re.search(r"(修改|更新|编辑|update|modify|edit)", text, re.IGNORECASE):
            action = "修改"
        elif re.search(r"(写|创建|新建|生成|新增|添加|create|write|add)", text, re.IGNORECASE):
            action = "创建"
        elif re.search(r"(删除|delete)", text, re.IGNORECASE):
            action = "删除"
        elif re.search(r"(运行|执行|run|execute)", text, re.IGNORECASE):
            action = "执行"
        elif re.search(r"(测试|test)", text, re.IGNORECASE):
            action = "测试"
        if action and file_name:
            return f"{action} {file_name}"
        return text[:32]

    @classmethod
    def _extract_file_refs(cls, text: str) -> list[str]:
        refs: list[str] = []
        for match in cls._FILE_REF_PATTERN.finditer(str(text or "")):
            value = str(match.group(1) or "").strip().lower()
            if value and value not in refs:
                refs.append(value)
        return refs

    @classmethod
    def _iter_task_snapshots(cls, task_system: SessionRuntime | None) -> list[dict[str, Any]]:
        if task_system is None:
            return []
        snapshots: list[dict[str, Any]] = []
        seen: set[str] = set()

        for task in task_system.active_tasks():
            snapshot = task.snapshot()
            task_id = str(snapshot.get("task_id", "") or "").strip()
            if task_id and task_id not in seen:
                snapshots.append(snapshot)
                seen.add(task_id)

        for snapshot in reversed(task_system.recent_task_snapshots()):
            task_id = str(snapshot.get("task_id", "") or "").strip()
            if task_id and task_id not in seen:
                snapshots.append(snapshot)
                seen.add(task_id)
        return snapshots

    @classmethod
    def _find_relevant_task_snapshot(
        cls,
        user_input: str,
        task_system: SessionRuntime | None,
        *,
        active_only: bool = False,
    ) -> dict[str, Any] | None:
        if task_system is None:
            return None

        snapshots: list[dict[str, Any]]
        if active_only:
            snapshots = [task.snapshot() for task in task_system.active_tasks()]
        else:
            snapshots = cls._iter_task_snapshots(task_system)
        if not snapshots:
            return None

        refs = cls._extract_file_refs(user_input)
        if refs:
            for snapshot in snapshots:
                haystacks = [
                    str(snapshot.get("title", "") or "").lower(),
                    str((snapshot.get("params") or {}).get("request", "") or "").lower(),
                    str((snapshot.get("params") or {}).get("title", "") or "").lower(),
                    str(snapshot.get("summary", "") or "").lower(),
                ]
                if any(ref in haystack for ref in refs for haystack in haystacks if haystack):
                    return snapshot

        normalized = " ".join(str(user_input or "").lower().split())
        for snapshot in snapshots:
            request = " ".join(str((snapshot.get("params") or {}).get("request", "") or "").lower().split())
            title = " ".join(str(snapshot.get("title", "") or "").lower().split())
            if normalized and ((request and normalized in request) or (title and normalized in title)):
                return snapshot

        if len(snapshots) == 1:
            return snapshots[0]
        return None

    @classmethod
    def _is_task_status_query(cls, user_input: str, task_system: SessionRuntime | None) -> bool:
        if task_system is None:
            return False
        text = str(user_input or "").strip()
        if not text:
            return False
        if not cls._STATUS_QUERY_PATTERN.search(text):
            return False
        return bool(task_system.active_tasks() or task_system.latest_task_snapshot())

    @staticmethod
    def _build_task_status_reply(
        snapshot: dict[str, Any] | None,
        *,
        multiple_active_summary: str = "",
        message_id: str = "",
    ) -> BrainControlPacket:
        if snapshot is None:
            final_message = multiple_active_summary or "当前没有正在执行的任务。"
            return {
                "message_id": str(message_id or "").strip(),
                "intent": "query_task_status",
                "working_hypothesis": "用户在询问任务状态。",
                "task_action": "none",
                "task_reason": "直接返回当前任务状态即可。",
                "final_decision": "answer",
                "final_message": final_message,
                "task_brief": "",
                "execution_summary": "返回任务状态。",
                "notify_user": True,
                "retrieval_query": "",
                "retrieval_focus": [],
                "retrieved_memory_ids": [],
            }

        title = str(snapshot.get("title", "") or snapshot.get("task_id", "") or "任务").strip()
        status = str(snapshot.get("status", "") or "").strip()
        summary = str(snapshot.get("summary", "") or "").strip()
        error = str(snapshot.get("error", "") or "").strip()
        stage_info = str(snapshot.get("stage_info", "") or "").strip()
        recommended_action = str(snapshot.get("recommended_action", "") or "").strip()
        input_request = snapshot.get("input_request") if isinstance(snapshot.get("input_request"), dict) else {}
        question = str(input_request.get("question", "") or "").strip()

        if status == "done":
            final_message = f"「{title}」已经完成。{summary or '处理完成。'}"
        elif status == "failed":
            final_message = f"「{title}」执行失败了。{error or summary or '暂时没有更多错误信息。'}"
        elif status == "waiting_input":
            final_message = f"「{title}」现在卡在等补充信息。{question or recommended_action or '请补充后再继续。'}"
        elif multiple_active_summary:
            final_message = multiple_active_summary
        else:
            final_message = f"「{title}」还在处理中。当前进度：{stage_info or '正在执行内部任务'}。"

        return {
            "message_id": str(message_id or "").strip(),
            "intent": "query_task_status",
            "working_hypothesis": "用户在询问任务状态。",
            "task_action": "none",
            "task_reason": "直接返回当前任务状态即可。",
            "final_decision": "answer",
            "final_message": final_message,
            "task_brief": "",
            "execution_summary": "返回任务状态。",
            "notify_user": True,
            "retrieval_query": "",
            "retrieval_focus": [],
            "retrieved_memory_ids": [],
        }

    @classmethod
    def _is_simple_greeting(cls, user_input: str) -> bool:
        return bool(cls._GREETING_PATTERN.match(str(user_input or "").strip()))

    @staticmethod
    def _build_greeting_reply(
        *,
        message_id: str = "",
        task_system: SessionRuntime | None = None,
    ) -> BrainControlPacket:
        active_tasks = task_system.active_tasks() if task_system is not None else []
        if active_tasks:
            latest = active_tasks[-1].snapshot()
            title = str(latest.get("title", "") or latest.get("task_id", "") or "任务").strip()
            stage = str(latest.get("stage_info", "") or "").strip() or "正在处理中"
            final_message = f"你好呀，我在。顺便说一下，「{title}」还在处理中，当前进度：{stage}。你可以继续说，我这边不会断。"
        else:
            final_message = "你好呀，我在。你现在想聊什么，或者要我继续处理什么？"
        return {
            "message_id": str(message_id or "").strip(),
            "intent": "simple_greeting",
            "working_hypothesis": "用户在进行轻量打招呼。",
            "task_action": "none",
            "task_reason": "轻量寒暄直接回复即可，不需要调用主脑工具链。",
            "final_decision": "answer",
            "final_message": final_message,
            "task_brief": "",
            "execution_summary": "回复用户问候。",
            "notify_user": True,
            "retrieval_query": "",
            "retrieval_focus": [],
            "retrieved_memory_ids": [],
        }

    @classmethod
    def _looks_like_waiting_task_answer(cls, user_input: str, task_system: SessionRuntime | None) -> bool:
        if task_system is None or task_system.waiting_task() is None:
            return False
        text = str(user_input or "").strip()
        if not text or text.startswith("/"):
            return False
        if cls._WAITING_TASK_CANCEL_PATTERN.search(text):
            return False
        if cls._is_simple_greeting(text):
            return False
        if cls._is_task_status_query(text, task_system):
            return False
        if cls._should_fast_dispatch_task(text, task_system):
            return False
        if cls._WAITING_TASK_QUESTION_PATTERN.search(text):
            return False
        return True

    @staticmethod
    def _build_waiting_task_fill_reply(
        *,
        waiting_task,
        answer: str,
        message_id: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainControlPacket:
        task_spec: TaskSpec = {
            "task_id": str(getattr(waiting_task, "task_id", "") or "").strip(),
            "origin_message_id": str(message_id or "").strip(),
            "title": str(getattr(waiting_task, "title", "") or "").strip(),
            "request": str(answer or "").strip(),
            "channel": str(channel or "").strip(),
            "chat_id": str(chat_id or "").strip(),
            "session_id": str(session_id or "").strip(),
        }
        params = getattr(waiting_task, "params", None)
        if isinstance(params, dict):
            for key in (
                "goal",
                "expected_output",
                "history_context",
                "constraints",
                "success_criteria",
                "memory_bundle_ids",
                "skill_hints",
                "media",
                "history",
                "task_context",
            ):
                value = params.get(key)
                if value not in (None, "", [], {}):
                    task_spec[key] = value

        title = str(task_spec.get("title", "") or task_spec.get("task_id", "") or "任务").strip()
        return {
            "message_id": str(message_id or "").strip(),
            "intent": "fill_waiting_task",
            "working_hypothesis": "当前有任务在等待补充信息，用户这句话是在直接补充所需内容。",
            "task_action": "fill_task",
            "task_reason": "直接将补充信息恢复给等待中的任务，避免主脑重复判断。",
            "final_decision": "continue",
            "final_message": f"收到，我继续处理「{title}」，有结果再告诉你。",
            "task_brief": f"已补充等待任务：{title}",
            "task": task_spec,
            "execution_summary": "已将用户补充信息提交给等待任务，继续异步执行。",
            "notify_user": True,
            "retrieval_query": "",
            "retrieval_focus": [],
            "retrieved_memory_ids": [],
        }

    @staticmethod
    def _should_fast_dispatch_task(user_input: str, task_system: SessionRuntime | None) -> bool:
        if task_system is None or task_system.waiting_task() is not None:
            return False
        text = str(user_input or "").strip()
        if not text:
            return False
        return bool(
            CompanionBrain._TASK_VERB_PATTERN.search(text)
            and CompanionBrain._CODE_SIGNAL_PATTERN.search(text)
        )

    async def _create_session_task(
        self,
        *,
        task_system: SessionRuntime,
        current_context: dict[str, Any],
        request: str,
        title: str = "",
        history_context: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> TaskSpec:
        task_id = f"task_{uuid4().hex[:12]}"
        task_title = self._derive_task_title(request, title)
        history = current_context.get("history", [])
        media = current_context.get("media", [])
        message_id = current_context.get("message_id", "")
        task_spec: TaskSpec = {
            "task_id": task_id,
            "origin_message_id": str(message_id or "").strip(),
            "title": task_title,
            "request": str(request or "").strip(),
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
        return task_spec

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

        if self._is_simple_greeting(user_input):
            return self._build_greeting_reply(message_id=message_id, task_system=task_system)

        if self._is_task_status_query(user_input, task_system):
            active_snapshots = [task.snapshot() for task in (task_system.active_tasks() if task_system is not None else [])]
            matching_snapshot = self._find_relevant_task_snapshot(user_input, task_system)
            if matching_snapshot is not None:
                return self._build_task_status_reply(matching_snapshot, message_id=message_id)
            if len(active_snapshots) > 1 and task_system is not None:
                return self._build_task_status_reply(
                    None,
                    multiple_active_summary="当前有多个任务在处理中：\n" + task_system.get_tasks_summary(),
                    message_id=message_id,
                )
            latest_snapshot = task_system.latest_task_snapshot() if task_system is not None else None
            return self._build_task_status_reply(latest_snapshot, message_id=message_id)

        waiting_task = task_system.waiting_task() if task_system is not None else None
        if waiting_task is not None and self._looks_like_waiting_task_answer(user_input, task_system):
            success = await task_system.answer(
                user_input,
                waiting_task.task_id,
                origin_message_id=str(message_id or "").strip(),
            )
            if success:
                return self._build_waiting_task_fill_reply(
                    waiting_task=waiting_task,
                    answer=user_input,
                    message_id=message_id,
                    channel=channel,
                    chat_id=chat_id,
                    session_id=session_id,
                )

        if self._should_fast_dispatch_task(user_input, task_system):
            existing_task = self._find_relevant_task_snapshot(user_input, task_system, active_only=True)
            if existing_task is not None:
                return self._build_task_status_reply(existing_task, message_id=message_id)
            task_spec = await self._create_session_task(
                task_system=task_system,
                current_context=current_context,
                request=user_input,
                channel=channel,
                chat_id=chat_id,
                session_id=session_id,
            )
            title = str(task_spec.get("title", "") or "").strip() or "任务"
            return {
                "message_id": str(message_id or "").strip(),
                "intent": "dispatch_explicit_task",
                "working_hypothesis": "用户给出的是明确的执行请求，适合直接委托给 central 异步处理。",
                "task_action": "create_task",
                "task_reason": "这是明确的文件/代码执行请求，不需要主脑继续同步展开。",
                "final_decision": "continue",
                "final_message": f"我先去处理「{title}」，有结果再告诉你。",
                "task_brief": f"已创建异步任务：{title}",
                "task": dict(task_spec),
                "execution_summary": "已将本轮请求委托给 central 异步执行。",
                "notify_user": True,
                "retrieval_query": "",
                "retrieval_focus": [],
                "retrieved_memory_ids": [],
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
