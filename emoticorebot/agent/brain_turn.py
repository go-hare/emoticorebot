"""First-pass brain turn understanding and routing helpers."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from emoticorebot.agent.reply_utils import build_companion_prompt
from emoticorebot.agent.state import BrainControlPacket, BrainUnderstandingPacket
from emoticorebot.utils.llm_utils import extract_message_metrics, extract_message_text

if TYPE_CHECKING:
    from emoticorebot.agent.brain import BrainService


def compact_text(text: Any, limit: int = 160) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."


def looks_task_like(user_input: str) -> bool:
    text = (user_input or "").lower()
    keywords = [
        "help me",
        "please",
        "find",
        "search",
        "plan",
        "remind",
        "schedule",
        "analyze",
        "generate",
        "run",
        "open",
        "weather",
        "price",
        "how",
        "why",
        "?",
        "？",
        "帮我",
        "请",
        "查",
        "搜索",
        "计划",
        "提醒",
        "安排",
        "分析",
        "生成",
        "运行",
        "打开",
        "天气",
        "价格",
        "怎么",
        "为什么",
    ]
    return any(keyword in text for keyword in keywords)


async def understand_turn(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> BrainUnderstandingPacket:
    lightweight_chat = not looks_task_like(user_input)
    prompt = _build_understanding_prompt(user_input=user_input, lightweight_chat=lightweight_chat)
    raw_text, metrics = await _run_brain_task(
        service,
        history=history,
        current_message=prompt,
        current_emotion=emotion,
        pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        internal_task_summaries=None,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
        query=user_input,
        retrieval_focus=["user", "relationship"] if lightweight_chat else ["user", "goal", "constraint", "tool", "skill"],
    )

    parsed = _parse_json(raw_text)
    if parsed is None:
        recovered = _recover_understanding(raw_text)
        if recovered is not None:
            recovered.update(metrics)
            return recovered
        fallback = _fallback_understanding(user_input=user_input)
        fallback.update(metrics)
        return fallback

    normalized = _normalize_understanding_payload(parsed)
    if normalized is None:
        fallback = _fallback_understanding(user_input=user_input)
        fallback.update(metrics)
        return fallback
    normalized.update(metrics)
    return normalized


async def decide_turn_action(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    understanding: BrainUnderstandingPacket,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> BrainControlPacket:
    turn_path = str(understanding.get("turn_path", "answer") or "answer").strip().lower()
    prompt = _build_turn_action_prompt(
        user_input=user_input,
        understanding=understanding,
        turn_path=turn_path,
    )
    raw_text, metrics = await _run_brain_task(
        service,
        history=history,
        current_message=prompt,
        current_emotion=emotion,
        pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        internal_task_summaries=None,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
        query=user_input,
        retrieval_focus=["user", "relationship"] if turn_path == "answer" else ["user", "goal", "constraint", "tool", "skill"],
    )

    parsed = _parse_json(raw_text)
    if parsed is None:
        recovered = _recover_turn_action(raw_text, understanding=understanding)
        if recovered is not None:
            recovered.update(metrics)
            return recovered
        fallback = _fallback_turn_action(
            understanding=understanding,
            emotion=emotion,
        )
        fallback.update(metrics)
        return fallback

    normalized = _normalize_turn_action_payload(parsed, understanding=understanding)
    if normalized is None:
        fallback = _fallback_turn_action(
            understanding=understanding,
            emotion=emotion,
        )
        fallback.update(metrics)
        return fallback
    normalized.update(metrics)
    return normalized


def _build_understanding_prompt(*, user_input: str, lightweight_chat: bool) -> str:
    if lightweight_chat:
        return f"""
你是 `brain`，正在先理解这一轮用户输入，再决定后续路径。

这一轮更像陪伴聊天或轻量交流，因此默认 `turn_path` 应为 `answer`。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `intent`：你对用户当前意图的简短理解。
- `working_hypothesis`：你当前的工作性判断，1 句话即可。
- `turn_path`：这里只能填 `answer`。
- `path_reason`：为什么这轮适合由 brain 直接处理。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "turn_path": "answer",
  "path_reason": "..."
}}

用户输入：{user_input}
""".strip()

    return f"""
你是 `brain`，正在先理解这一轮用户输入，再决定后续路径。

请先理解用户真实意图、情绪与问题复杂度，然后只判断这一轮更适合：
- `answer`：由 `brain` 直接处理；
- `task`：交给 `central` 做事实核查、工具执行或多步求解。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `intent`：你对用户当前意图的简要理解。
- `working_hypothesis`：你目前对问题的工作性判断。
- `turn_path`：只能是 `answer` 或 `task`。
- `path_reason`：为什么选择这条路径。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "turn_path": "answer|task",
  "path_reason": "..."
}}

用户输入：{user_input}
""".strip()


def _build_turn_action_prompt(
    *,
    user_input: str,
    understanding: BrainUnderstandingPacket,
    turn_path: str,
) -> str:
    intent = str(understanding.get("intent", "") or "").strip()
    working_hypothesis = compact_text(understanding.get("working_hypothesis", ""), limit=180)
    path_reason = compact_text(understanding.get("path_reason", ""), limit=180)
    if turn_path == "task":
        return f"""
你是 `brain`，已经完成了当前轮的理解。现在只需要把这轮转成明确的下一步动作。

当前理解：
- intent：{intent or '（空）'}
- working_hypothesis：{working_hypothesis or '（空）'}
- turn_path：task
- path_reason：{path_reason or '（空）'}

请把它转成一条高质量的内部委托，交给 `central`。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `action`：这里只能填 `create_task`。
- `reason`：为什么要创建 task。
- `final_decision`：这里只能填 `continue`。
- `message`：必须为空字符串 `""`。
- `task_brief`：发给 `central` 的内部请求，必须非空，聚焦事实、执行、风险与下一步。

标准结构：
{{
  "action": "create_task",
  "reason": "...",
  "final_decision": "continue",
  "message": "",
  "task_brief": "..."
}}

用户输入：{user_input}
""".strip()

    return f"""
你是 `brain`，已经完成了当前轮的理解。现在只需要把这轮转成明确的下一步动作。

当前理解：
- intent：{intent or '（空）'}
- working_hypothesis：{working_hypothesis or '（空）'}
- turn_path：answer
- path_reason：{path_reason or '（空）'}

请直接生成这一轮要回给用户的话。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `action`：这里只能填 `none`。
- `reason`：为什么由 `brain` 直接回复。
- `final_decision`：这里只能填 `answer`。
- `message`：真正要发给用户的话，必须使用与用户相同的语言，必须非空。
- `task_brief`：必须为空字符串 `""`。

标准结构：
{{
  "action": "none",
  "reason": "...",
  "final_decision": "answer",
  "message": "...",
  "task_brief": ""
}}

用户输入：{user_input}
""".strip()


async def _run_brain_task(
    service: "BrainService",
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
    records = service.context.query_brain_memories(query=query, limit=8)
    messages = service.context.build_messages(
        history=history,
        current_message=current_message,
        current_emotion=current_emotion,
        pad_state=pad_state,
        internal_task_summaries=internal_task_summaries,
        query=query,
    )
    response = await service.brain_llm.ainvoke(messages)
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


def _normalize_understanding_payload(parsed: dict[str, Any]) -> BrainUnderstandingPacket | None:
    if not isinstance(parsed, dict):
        return None

    intent = str(parsed.get("intent", "") or "").strip()
    working_hypothesis = str(parsed.get("working_hypothesis", "") or "").strip()
    turn_path = str(parsed.get("turn_path", "") or "").strip().lower()
    path_reason = str(parsed.get("path_reason", "") or "").strip()

    if turn_path not in {"answer", "task"}:
        task_action = str(parsed.get("task_action", "") or "").strip().lower()
        needs_task = parsed.get("needs_task")
        if task_action in {"create_task", "start"}:
            turn_path = "task"
        elif task_action in {"none", "answer"}:
            turn_path = "answer"
        elif isinstance(needs_task, bool):
            turn_path = "task" if needs_task else "answer"
        else:
            return None
        if not path_reason:
            path_reason = str(parsed.get("task_reason", "") or "").strip()

    return {
        "intent": intent,
        "working_hypothesis": working_hypothesis,
        "turn_path": turn_path,
        "path_reason": path_reason,
    }


def _normalize_turn_action_payload(
    parsed: dict[str, Any],
    *,
    understanding: BrainUnderstandingPacket,
) -> BrainControlPacket | None:
    if not isinstance(parsed, dict):
        return None

    action = str(parsed.get("action", "") or parsed.get("task_action", "") or "").strip().lower()
    if action == "answer":
        action = "none"
    if action == "start":
        action = "create_task"

    turn_path = str(understanding.get("turn_path", "answer") or "answer").strip().lower()
    if action not in {"none", "create_task"}:
        action = "create_task" if turn_path == "task" else "none"

    final_decision = str(parsed.get("final_decision", "") or "").strip().lower()
    if final_decision not in {"answer", "continue"}:
        final_decision = "continue" if action == "create_task" else "answer"

    reason = str(parsed.get("reason", "") or parsed.get("task_reason", "") or "").strip()
    message = str(parsed.get("message", "") or parsed.get("final_message", "") or "").strip()
    task_brief = str(parsed.get("task_brief", "") or "").strip()

    if action == "create_task":
        final_decision = "continue"
        if not task_brief:
            task_brief = build_default_task_brief(
                working_hypothesis=str(understanding.get("working_hypothesis", "") or ""),
                intent=str(understanding.get("intent", "") or ""),
            )
        message = ""
    else:
        action = "none"
        final_decision = "answer"
        task_brief = ""
        if not message:
            return None

    return {
        "action": action,
        "reason": reason or str(understanding.get("path_reason", "") or "").strip(),
        "final_decision": final_decision,
        "message": message,
        "task_brief": task_brief,
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    raw = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _extract_json_string_field(raw: str, field: str) -> str:
    pattern = rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, raw, flags=re.DOTALL)
    if not match:
        return ""
    value = match.group(1)
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.replace("\\n", "\n").replace('\\"', '"').strip()


def _extract_json_bool_field(raw: str, field: str) -> bool | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', raw, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _recover_understanding(raw: str) -> BrainUnderstandingPacket | None:
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    turn_path = _extract_json_string_field(cleaned, "turn_path").lower()
    path_reason = _extract_json_string_field(cleaned, "path_reason")
    task_action = _extract_json_string_field(cleaned, "task_action").lower()
    task_reason = _extract_json_string_field(cleaned, "task_reason")
    needs_task = _extract_json_bool_field(cleaned, "needs_task")
    intent = _extract_json_string_field(cleaned, "intent")
    working_hypothesis = _extract_json_string_field(cleaned, "working_hypothesis")

    if turn_path in {"answer", "task"}:
        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "turn_path": turn_path,
            "path_reason": path_reason,
        }
    if task_action in {"start", "create_task"}:
        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "turn_path": "task",
            "path_reason": task_reason,
        }
    if task_action in {"answer", "none"}:
        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "turn_path": "answer",
            "path_reason": task_reason,
        }
    if needs_task is not None:
        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "turn_path": "task" if needs_task else "answer",
            "path_reason": task_reason,
        }
    return None


def _recover_turn_action(
    raw: str,
    *,
    understanding: BrainUnderstandingPacket,
) -> BrainControlPacket | None:
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    parsed = {
        "action": _extract_json_string_field(cleaned, "action"),
        "task_action": _extract_json_string_field(cleaned, "task_action"),
        "reason": _extract_json_string_field(cleaned, "reason"),
        "task_reason": _extract_json_string_field(cleaned, "task_reason"),
        "final_decision": _extract_json_string_field(cleaned, "final_decision"),
        "message": _extract_json_string_field(cleaned, "message"),
        "final_message": _extract_json_string_field(cleaned, "final_message"),
        "task_brief": _extract_json_string_field(cleaned, "task_brief"),
    }
    normalized = _normalize_turn_action_payload(parsed, understanding=understanding)
    if normalized is not None:
        return normalized
    return None


def _fallback_understanding(*, user_input: str) -> BrainUnderstandingPacket:
    if looks_task_like(user_input):
        return {
            "intent": "用户需要事实分析、执行帮助，或更强的问题求解。",
            "working_hypothesis": "在给出最终表达前，需要先调用 central 补齐事实与执行判断。",
            "turn_path": "task",
            "path_reason": "需要工具、事实核查或多步执行。",
        }
    return {
        "intent": "用户当前更需要陪伴式回应或轻量交流。",
        "working_hypothesis": "这一轮无需调用 central。",
        "turn_path": "answer",
        "path_reason": "这轮更适合由 brain 直接承接。",
    }


def _fallback_turn_action(
    *,
    understanding: BrainUnderstandingPacket,
    emotion: str,
) -> BrainControlPacket:
    turn_path = str(understanding.get("turn_path", "answer") or "answer").strip().lower()
    reason = str(understanding.get("path_reason", "") or "").strip()
    if turn_path == "task":
        return {
            "action": "create_task",
            "reason": reason or "brain_requested_task",
            "final_decision": "continue",
            "message": "",
            "task_brief": build_default_task_brief(
                working_hypothesis=str(understanding.get("working_hypothesis", "") or ""),
                intent=str(understanding.get("intent", "") or ""),
            ),
        }
    return {
        "action": "none",
        "reason": reason or "brain_answered_directly",
        "final_decision": "answer",
        "message": build_companion_prompt(emotion),
        "task_brief": "",
    }


def build_default_task_brief(*, working_hypothesis: str, intent: str) -> str:
    if working_hypothesis:
        return (
            "Analyze the current working hypothesis, identify evidence, risks, "
            f"and the best next action: {working_hypothesis}"
        )
    if intent:
        return (
            "Analyze this user intent, identify evidence, risks, "
            f"and the best next action: {intent}"
        )
    return "Analyze the current internal question and return evidence, risks, and the best next action."


__all__ = [
    "brain_default_task_brief",
    "compact_text",
    "decide_turn_action",
    "looks_task_like",
    "understand_turn",
]

brain_default_task_brief = build_default_task_brief
