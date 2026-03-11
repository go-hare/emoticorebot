"""Thin main-brain service with a small public API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from emoticorebot.agent.brain_shared import compact_text, extract_json_string_field, parse_json_dict
from emoticorebot.agent.brain_types import BrainControlPacket
from emoticorebot.agent.context import ContextBuilder
from emoticorebot.agent.reply_utils import build_companion_prompt
from emoticorebot.runtime.event_bus import RuntimeEventBus
from emoticorebot.utils.llm_utils import extract_message_metrics, extract_message_text

if TYPE_CHECKING:
    from emoticorebot.agent.system import SessionTaskSystem


BRAIN_MESSAGE_RETRIEVAL_FOCUS = ["user", "relationship", "goal", "constraint", "tool", "skill"]


def build_default_brain_task_brief(*, working_hypothesis: str, intent: str) -> str:
    if working_hypothesis:
        return (
            "Analyze the current working hypothesis, identify evidence, risks, "
            f"and the best next action: {working_hypothesis}"
        )
    if intent:
        return f"Analyze this user intent, identify evidence, risks, and the best next action: {intent}"
    return "Analyze the current internal question and return evidence, risks, and the best next action."


def _build_brain_message_prompt(
    *,
    user_input: str,
    has_waiting_task: bool,
    waiting_task_hint: dict[str, Any] | None,
) -> str:
    waiting_block = "当前没有等待补充参数的任务。"
    if has_waiting_task:
        hint = dict(waiting_task_hint or {})
        missing = [str(item).strip() for item in list(hint.get("missing", []) or []) if str(item).strip()]
        question = str(hint.get("question", "") or "").strip()
        waiting_block = (
            "当前存在一个等待补充参数的任务。\n"
            f"- 缺少信息：{missing or []}\n"
            f"- 当前追问：{question or '（空）'}"
        )

    return f"""
你是 `brain`，现在只做一件事：收到用户消息后，直接判断这一轮该怎么走。

你只允许做三种动作：
- `none`：这轮由 brain 直接回复用户，属于普通聊天、解释、澄清或直接回答。
- `create_task`：这轮需要创建一个新的 `central` 任务。
- `fill_task`：这轮用户输入是在补充当前等待任务所缺的参数或信息。

任务系统状态：
{waiting_block}

判断规则：
1. 先理解用户这一轮真正想做什么。
2. 如果用户是在补充当前等待任务，就选 `fill_task`。
3. 如果是新的复杂问题、需要执行、查找、分析、多步处理，就选 `create_task`。
4. 其余情况选 `none`。
5. 不要同时做多个动作。
6. 只有 `none` 时，`message` 才必须是对用户可见的话。
7. `create_task` 或 `fill_task` 时，`message` 可以为空字符串。
8. `create_task` 时，`task_brief` 必须非空；否则必须为空字符串。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "action": "none|create_task|fill_task",
  "reason": "...",
  "final_decision": "answer|ask_user|continue",
  "message": "...",
  "task_brief": "..."
}}

字段约束：
- `intent`：你对当前轮用户意图的简要理解。
- `working_hypothesis`：你的当前工作性判断。
- `action=none` 时：`final_decision` 只能是 `answer` 或 `ask_user`。
- `action=create_task` 时：`final_decision` 必须是 `continue`。
- `action=fill_task` 时：`final_decision` 必须是 `answer`。

用户输入：{user_input}
""".strip()


def _extract_string_fields(
    raw: str,
    *,
    fields: tuple[str, ...],
    lowercase: tuple[str, ...] = (),
) -> dict[str, str]:
    cleaned = raw.strip()
    payload: dict[str, str] = {}
    for field in fields:
        value = extract_json_string_field(cleaned, field)
        payload[field] = value.lower() if field in lowercase else value
    return payload


def _normalize_brain_control_payload(
    parsed: dict[str, Any],
    *,
    has_waiting_task: bool,
) -> BrainControlPacket | None:
    if not isinstance(parsed, dict):
        return None

    intent = str(parsed.get("intent", "") or "").strip()
    working_hypothesis = str(parsed.get("working_hypothesis", "") or "").strip()
    action = str(parsed.get("action", "") or "").strip().lower()
    reason = str(parsed.get("reason", "") or "").strip()
    final_decision = str(parsed.get("final_decision", "") or "").strip().lower()
    message = str(parsed.get("message", "") or "").strip()
    task_brief = str(parsed.get("task_brief", "") or "").strip()

    if action not in {"none", "create_task", "fill_task"}:
        return None
    if not intent and not working_hypothesis:
        return None
    if not reason:
        return None

    if action == "fill_task":
        if not has_waiting_task or final_decision != "answer":
            return None
        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "action": "fill_task",
            "reason": reason,
            "final_decision": "answer",
            "message": "",
            "task_brief": "",
        }

    if action == "create_task":
        if final_decision != "continue":
            return None
        if not task_brief:
            task_brief = build_default_brain_task_brief(
                working_hypothesis=working_hypothesis,
                intent=intent,
            )
        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "action": "create_task",
            "reason": reason,
            "final_decision": "continue",
            "message": message,
            "task_brief": task_brief,
        }

    if final_decision not in {"answer", "ask_user"}:
        return None
    if not message:
        return None
    return {
        "intent": intent,
        "working_hypothesis": working_hypothesis,
        "action": "none",
        "reason": reason,
        "final_decision": final_decision,
        "message": message,
        "task_brief": "",
    }


def _fallback_brain_control(*, user_input: str, emotion: str) -> BrainControlPacket:
    return {
        "intent": compact_text(user_input, limit=80) or "普通交流",
        "working_hypothesis": "这轮先由 brain 直接承接。",
        "action": "none",
        "reason": "fallback_direct_answer",
        "final_decision": "answer",
        "message": build_companion_prompt(
            user_input="",
            emotion=emotion,
            short=True,
        ),
        "task_brief": "",
    }


async def decide_brain_message(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    has_waiting_task: bool,
    waiting_task_hint: dict[str, Any] | None,
    channel: str,
    chat_id: str,
    session_id: str,
) -> BrainControlPacket:
    prompt = _build_brain_message_prompt(
        user_input=user_input,
        has_waiting_task=has_waiting_task,
        waiting_task_hint=waiting_task_hint,
    )
    raw_text, metrics = await service._run_brain_task(
        history=history,
        current_message=prompt,
        current_emotion=emotion,
        pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        internal_task_summaries=None,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
        query=user_input,
        retrieval_focus=BRAIN_MESSAGE_RETRIEVAL_FOCUS,
    )
    result = _normalize_brain_control_payload(parse_json_dict(raw_text) or {}, has_waiting_task=has_waiting_task)
    if result is None:
        result = _normalize_brain_control_payload(
            _extract_string_fields(
                raw_text,
                fields=("intent", "working_hypothesis", "action", "reason", "final_decision", "message", "task_brief"),
                lowercase=("action", "final_decision"),
            ),
            has_waiting_task=has_waiting_task,
        )
    if result is None:
        result = _fallback_brain_control(user_input=user_input, emotion=emotion)
    result.update(metrics)
    return result


TASK_EVENT_SPECS: dict[str, dict[str, str]] = {
    "need_input": {
        "field": "question",
        "fallback": "继续之前我需要你补充一些信息。",
        "reason": "task_needs_input",
        "final_decision": "ask_user",
    },
    "done": {
        "field": "summary",
        "fallback": "已经处理完成。",
        "reason": "task_completed",
        "final_decision": "answer",
    },
    "failed": {
        "field": "reason",
        "fallback": "执行出了点问题。",
        "reason": "task_failed",
        "final_decision": "answer",
    },
}


def _should_forward_task_stage_event(event: dict[str, Any]) -> bool:
    payload = dict(event.get("payload", {}) or {})
    signal_event = str(payload.get("event", "") or event.get("event", "") or "").strip().lower()
    phase = str(payload.get("phase", "") or "").strip().lower()
    if signal_event == "task.stage":
        return True
    return phase == "stage"


def _normalize_brain_message_metadata(control: BrainControlPacket, *, user_input: str) -> BrainControlPacket:
    control["retrieval_query"] = str(control.get("retrieval_query", "") or user_input)
    control["retrieval_focus"] = list(control.get("retrieval_focus", []) or BRAIN_MESSAGE_RETRIEVAL_FOCUS)
    control["retrieved_memory_ids"] = list(control.get("retrieved_memory_ids", []) or [])
    return control


class BrainService:
    """Brain entrypoints: runtime message, task event, human interaction."""

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

    @staticmethod
    def build_runtime_brain_snapshot(
        *,
        control: BrainControlPacket,
        emotion: str,
        pad: dict[str, float],
        default_query: str,
    ) -> Any:
        return SimpleNamespace(
            emotion=str(emotion or "平静").strip() or "平静",
            pad=dict(pad or {}),
            intent=str(control.get("intent", "") or ""),
            working_hypothesis=str(control.get("working_hypothesis", "") or ""),
            retrieval_query=str(control.get("retrieval_query", "") or default_query),
            retrieval_focus=[
                str(item).strip()
                for item in list(control.get("retrieval_focus", []) or [])
                if str(item).strip()
            ],
            retrieved_memory_ids=[
                str(item).strip()
                for item in list(control.get("retrieved_memory_ids", []) or [])
                if str(item).strip()
            ],
            task_brief=str(control.get("task_brief", "") or ""),
            final_decision=str(control.get("final_decision", "") or ""),
            final_message=str(control.get("message", "") or ""),
            task_action=str(control.get("action", "") or ""),
            task_reason=str(control.get("reason", "") or ""),
            model_name=str(control.get("model_name", "") or ""),
            prompt_tokens=int(control.get("prompt_tokens", 0) or 0),
            completion_tokens=int(control.get("completion_tokens", 0) or 0),
            total_tokens=int(control.get("total_tokens", 0) or 0),
        )

    async def _handle_fill_task_action(
        self,
        *,
        control: BrainControlPacket,
        user_input: str,
        waiting_task,
        task_system: "SessionTaskSystem",
        session_id: str,
        message_id: str,
        channel: str,
        chat_id: str,
    ) -> BrainControlPacket | None:
        answered = await task_system.answer(user_input, waiting_task.task_id)
        if not answered:
            return None
        message = await self.handle_human_interaction(
            content=str(control.get("message", "") or "").strip() or "收到，我继续处理。",
            session_id=session_id,
            message_id=message_id,
            task_id=waiting_task.task_id,
            channel=channel,
            chat_id=chat_id,
            event="brain.message",
            payload={
                "producer": "brain",
                "final_decision": "answer",
                "reason": "user_provided_waiting_task_input",
            },
        )
        return {
            "action": "none",
            "reason": "user_provided_waiting_task_input",
            "final_decision": "answer",
            "message": message,
            "intent": str(control.get("intent", "") or ""),
            "working_hypothesis": str(control.get("working_hypothesis", "") or ""),
            "retrieval_query": str(control.get("retrieval_query", "") or user_input),
            "retrieval_focus": list(control.get("retrieval_focus", []) or BRAIN_MESSAGE_RETRIEVAL_FOCUS),
            "retrieved_memory_ids": list(control.get("retrieved_memory_ids", []) or []),
        }

    async def _handle_create_task_action(
        self,
        *,
        control: BrainControlPacket,
        history: list[dict[str, Any]],
        task_system: "SessionTaskSystem",
        session_id: str,
        message_id: str,
        channel: str,
        chat_id: str,
    ) -> BrainControlPacket:
        task_brief = str(control.get("task_brief", "") or "").strip() or build_default_brain_task_brief(
            working_hypothesis=str(control.get("working_hypothesis", "") or ""),
            intent=str(control.get("intent", "") or ""),
        )
        task_id = f"task_{uuid4().hex[:12]}"
        await task_system.create_central_task(
            task_id,
            request=task_brief,
            history=history,
            task_context=dict(control.get("task", {}) or {}),
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )
        ack = str(control.get("message", "") or "").strip() or "好，我来处理这件事，先开始做。"
        ack = await self.handle_human_interaction(
            content=ack,
            session_id=session_id,
            message_id=message_id,
            task_id=task_id,
            channel=channel,
            chat_id=chat_id,
            event="brain.message",
            payload={
                "producer": "brain",
                "final_decision": "answer",
                "reason": str(control.get("reason", "") or "created_task"),
            },
        )
        control["action"] = "none"
        control["reason"] = str(control.get("reason", "") or "created_task")
        control["final_decision"] = "answer"
        control["message"] = ack
        control["task"] = {"task_id": task_id}
        return control

    async def _finalize_brain_message(
        self,
        *,
        control: BrainControlPacket,
        session_id: str,
        message_id: str,
        channel: str,
        chat_id: str,
    ) -> BrainControlPacket:
        decision = str(control.get("final_decision", "") or "").strip()
        if decision not in {"answer", "ask_user"}:
            return control
        message = str(control.get("message", "") or "").strip()
        if not message:
            return control
        control["message"] = await self.handle_human_interaction(
            content=message,
            session_id=session_id,
            message_id=message_id,
            task_id=str((control.get("task", {}) or {}).get("task_id", "") or ""),
            channel=channel,
            chat_id=chat_id,
            event="brain.message",
            payload={
                "producer": "brain",
                "final_decision": decision,
                "reason": str(control.get("reason", "") or ""),
            },
        )
        return control

    async def handle_user_message(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        task_system: "SessionTaskSystem | None" = None,
        message_id: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainControlPacket:
        waiting_task = task_system.waiting_task() if task_system is not None else None
        control = _normalize_brain_message_metadata(
            await decide_brain_message(
            self,
            user_input=user_input,
            history=history,
            emotion=emotion,
            pad=pad,
            has_waiting_task=waiting_task is not None,
            waiting_task_hint={
                "missing": list(getattr(waiting_task, "missing", []) or []),
                "question": str((((getattr(waiting_task, "input_request", None) or {}).get("question", "")) or "")),
            }
            if waiting_task is not None
            else None,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
            ),
            user_input=user_input,
        )

        if str(control.get("action", "") or "") == "fill_task" and waiting_task is not None and task_system is not None:
            filled = await self._handle_fill_task_action(
                control=control,
                user_input=user_input,
                waiting_task=waiting_task,
                task_system=task_system,
                session_id=session_id,
                message_id=message_id,
                channel=channel,
                chat_id=chat_id,
            )
            if filled is not None:
                return filled

        action = str(control.get("action", "") or "").strip()

        if action == "create_task" and task_system is not None:
            return await self._handle_create_task_action(
                control=control,
                history=history,
                task_system=task_system,
                session_id=session_id,
                message_id=message_id,
                channel=channel,
                chat_id=chat_id,
            )

        return await self._finalize_brain_message(
            control=control,
            session_id=session_id,
            message_id=message_id,
            channel=channel,
            chat_id=chat_id,
        )

    async def handle_task_event(
        self,
        *,
        event: dict[str, Any],
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        message_id: str = "",
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainControlPacket:
        event_type = str(event.get("type", "") or "").strip()
        task_id = str(event.get("task_id", "") or "").strip()

        spec = TASK_EVENT_SPECS.get(event_type)
        if spec is not None:
            content = str(event.get(spec["field"], "") or "").strip() or spec["fallback"]
            message = await self.handle_human_interaction(
                content=content,
                session_id=session_id,
                message_id=message_id,
                task_id=task_id,
                channel=channel,
                chat_id=chat_id,
                event="brain.task_event",
                payload={"producer": "brain", "event_type": event_type},
            )
            return {
                "action": "none",
                "reason": spec["reason"],
                "final_decision": spec["final_decision"],
                "message": message,
            }

        if event_type == "progress" and _should_forward_task_stage_event(event):
            message = str(event.get("message", "") or event.get("content", "") or "").strip()
            if message:
                forwarded = await self.handle_human_interaction(
                    content=message,
                    session_id=session_id,
                    message_id=message_id,
                    task_id=task_id,
                    channel=channel,
                    chat_id=chat_id,
                    event="brain.task_event",
                    payload={"producer": "brain", "event_type": event_type},
                )
                return {
                    "action": "none",
                    "reason": "task_stage_shared",
                    "final_decision": "answer",
                    "message": forwarded,
                    "notify_user": True,
                }

        return {
            "action": "none",
            "reason": "task_event_ignored",
        }

    async def handle_human_interaction(
        self,
        *,
        content: str,
        session_id: str,
        message_id: str = "",
        task_id: str = "",
        channel: str = "",
        chat_id: str = "",
        event: str = "brain.human_interaction",
        payload: dict[str, Any] | None = None,
    ) -> str:
        del session_id, message_id, task_id, channel, chat_id, event, payload
        return str(content or "").strip()

    async def _generate_proactive(
        self,
        prompt: str,
        *,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> str:
        del channel, chat_id, session_id
        system_prompt = self.context.build_brain_system_prompt(query=prompt)
        response = await self.brain_llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
        )
        return extract_message_text(response).strip()

    async def _run_brain_task(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str,
        pad_state: tuple[float, float, float] | None,
        internal_task_summaries: list[str] | None,
        channel: str,
        chat_id: str,
        session_id: str,
        query: str,
        retrieval_focus: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        del channel, chat_id, session_id
        records = self.context.query_brain_memories(query=query, limit=8)
        messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_task_summaries=internal_task_summaries,
            query=query,
        )
        response = await self.brain_llm.ainvoke(messages)
        metrics = extract_message_metrics(response)
        metrics.update(
            {
                "retrieval_query": query,
                "retrieval_focus": list(retrieval_focus or []),
                "retrieved_memory_ids": [
                    str(record.get("id", "") or "")
                    for record in records
                    if str(record.get("id", "") or "")
                ],
            }
        )
        return extract_message_text(response), metrics


__all__ = ["BrainService"]
