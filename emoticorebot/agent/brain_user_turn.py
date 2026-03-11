"""User-turn planning for the brain layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from emoticorebot.agent.brain_shared import (
    compact_text,
    extract_json_bool_field,
    extract_json_string_field,
    parse_json_dict,
)
from emoticorebot.agent.reply_utils import build_companion_prompt
from emoticorebot.agent.state import BrainControlPacket, BrainUnderstandingPacket

if TYPE_CHECKING:
    from emoticorebot.agent.brain import BrainService


async def handle_user_turn(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    paused_task: dict[str, Any] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> BrainControlPacket:
    task = dict(paused_task or {})
    if str(task.get("control_state", "") or "").strip() == "paused":
        return _handle_paused_task(
            user_input=user_input,
            task=task,
            emotion=emotion,
        )

    understanding = await _understand_turn(
        service,
        user_input=user_input,
        history=history,
        emotion=emotion,
        pad=pad,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
    )
    control = await _decide_turn_action(
        service,
        user_input=user_input,
        history=history,
        emotion=emotion,
        pad=pad,
        understanding=understanding,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
    )
    control["intent"] = str(understanding.get("intent", "") or "")
    control["working_hypothesis"] = str(understanding.get("working_hypothesis", "") or "")
    control["retrieval_query"] = str(
        control.get("retrieval_query", "")
        or understanding.get("retrieval_query", "")
        or user_input
    )
    control["retrieval_focus"] = [
        str(item).strip()
        for item in list(control.get("retrieval_focus", []) or understanding.get("retrieval_focus", []) or [])
        if str(item).strip()
    ]
    control["retrieved_memory_ids"] = [
        str(item).strip()
        for item in list(
            control.get("retrieved_memory_ids", []) or understanding.get("retrieved_memory_ids", []) or []
        )
        if str(item).strip()
    ]
    if not str(control.get("model_name", "") or "").strip():
        control["model_name"] = str(understanding.get("model_name", "") or "")
    if not int(control.get("prompt_tokens", 0) or 0):
        control["prompt_tokens"] = int(understanding.get("prompt_tokens", 0) or 0)
    if not int(control.get("completion_tokens", 0) or 0):
        control["completion_tokens"] = int(understanding.get("completion_tokens", 0) or 0)
    if not int(control.get("total_tokens", 0) or 0):
        control["total_tokens"] = int(understanding.get("total_tokens", 0) or 0)
    return control


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


def build_default_task_brief(*, working_hypothesis: str, intent: str) -> str:
    if working_hypothesis:
        return (
            "Analyze the current working hypothesis, identify evidence, risks, "
            f"and the best next action: {working_hypothesis}"
        )
    if intent:
        return f"Analyze this user intent, identify evidence, risks, and the best next action: {intent}"
    return "Analyze the current internal question and return evidence, risks, and the best next action."


async def _understand_turn(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    channel: str,
    chat_id: str,
    session_id: str,
) -> BrainUnderstandingPacket:
    lightweight_chat = not looks_task_like(user_input)
    prompt = _build_understanding_prompt(user_input=user_input, lightweight_chat=lightweight_chat)
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
        retrieval_focus=["user", "relationship"] if lightweight_chat else ["user", "goal", "constraint", "tool", "skill"],
    )

    parsed = parse_json_dict(raw_text)
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


async def _decide_turn_action(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    understanding: BrainUnderstandingPacket,
    channel: str,
    chat_id: str,
    session_id: str,
) -> BrainControlPacket:
    turn_path = str(understanding.get("turn_path", "answer") or "answer").strip().lower()
    prompt = _build_turn_action_prompt(
        user_input=user_input,
        understanding=understanding,
        turn_path=turn_path,
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
        retrieval_focus=["user", "relationship"] if turn_path == "answer" else ["user", "goal", "constraint", "tool", "skill"],
    )

    parsed = parse_json_dict(raw_text)
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
你是 `brain`，已经完成了当前轮的理解。现在只需要直接给出本轮对用户的处理动作。

当前理解：
- intent：{intent or '（空）'}
- working_hypothesis：{working_hypothesis or '（空）'}
- turn_path：answer
- path_reason：{path_reason or '（空）'}

请只决定如何直接面对用户说话。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `action`：这里只能填 `none`。
- `reason`：为什么这轮不需要创建 task。
- `final_decision`：只能填 `answer`。
- `message`：给用户的回复，必须使用与用户相同的语言。
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


def _normalize_understanding_payload(parsed: dict[str, Any]) -> BrainUnderstandingPacket | None:
    if not isinstance(parsed, dict):
        return None

    intent = str(parsed.get("intent", "") or "").strip()
    working_hypothesis = str(parsed.get("working_hypothesis", "") or "").strip()
    turn_path = str(parsed.get("turn_path", "") or "").strip().lower()
    path_reason = str(parsed.get("path_reason", "") or "").strip()
    if turn_path not in {"answer", "task"}:
        return None
    if not intent and not working_hypothesis:
        return None
    if not path_reason:
        return None
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

    action = str(parsed.get("action", "") or "").strip().lower()
    reason = str(parsed.get("reason", "") or "").strip()
    final_decision = str(parsed.get("final_decision", "") or "").strip().lower()
    message = str(parsed.get("message", "") or "").strip()
    task_brief = str(parsed.get("task_brief", "") or "").strip()
    turn_path = str(understanding.get("turn_path", "answer") or "answer").strip().lower()

    if turn_path == "task":
        if action != "create_task" or final_decision != "continue":
            return None
        if not task_brief:
            task_brief = build_default_task_brief(
                working_hypothesis=str(understanding.get("working_hypothesis", "") or ""),
                intent=str(understanding.get("intent", "") or ""),
            )
        return {
            "action": "create_task",
            "reason": reason or str(understanding.get("path_reason", "") or "brain_routed_to_task"),
            "final_decision": "continue",
            "message": "",
            "task_brief": task_brief,
        }

    if final_decision != "answer":
        return None
    if not message:
        return None
    return {
        "action": "none",
        "reason": reason or str(understanding.get("path_reason", "") or "brain_answered_directly"),
        "final_decision": "answer",
        "message": message,
        "task_brief": "",
    }


def _recover_understanding(raw: str) -> BrainUnderstandingPacket | None:
    cleaned = raw.strip()
    intent = extract_json_string_field(cleaned, "intent")
    working_hypothesis = extract_json_string_field(cleaned, "working_hypothesis")
    turn_path = extract_json_string_field(cleaned, "turn_path").lower()
    path_reason = extract_json_string_field(cleaned, "path_reason")
    if turn_path not in {"answer", "task"}:
        return None
    if not path_reason:
        return None
    if not intent and not working_hypothesis:
        return None
    return {
        "intent": intent,
        "working_hypothesis": working_hypothesis,
        "turn_path": turn_path,
        "path_reason": path_reason,
    }


def _recover_turn_action(
    raw: str,
    *,
    understanding: BrainUnderstandingPacket,
) -> BrainControlPacket | None:
    cleaned = raw.strip()
    action = extract_json_string_field(cleaned, "action").lower()
    reason = extract_json_string_field(cleaned, "reason")
    final_decision = extract_json_string_field(cleaned, "final_decision").lower()
    message = extract_json_string_field(cleaned, "message")
    task_brief = extract_json_string_field(cleaned, "task_brief")
    payload = {
        "action": action,
        "reason": reason,
        "final_decision": final_decision,
        "message": message,
        "task_brief": task_brief,
    }
    return _normalize_turn_action_payload(payload, understanding=understanding)


def _fallback_understanding(*, user_input: str) -> BrainUnderstandingPacket:
    intent = compact_text(user_input, limit=80) or "陪伴交流"
    task_like = looks_task_like(user_input)
    return {
        "intent": intent,
        "working_hypothesis": "用户希望我先给出一个当前可执行的回应。" if not task_like else "这轮更像是需要分析或执行的任务请求。",
        "turn_path": "task" if task_like else "answer",
        "path_reason": "输入中包含明显的任务性诉求。" if task_like else "这轮更适合直接陪伴式回应。",
    }


def _fallback_turn_action(
    *,
    understanding: BrainUnderstandingPacket,
    emotion: str,
) -> BrainControlPacket:
    turn_path = str(understanding.get("turn_path", "answer") or "answer").strip().lower()
    if turn_path == "task":
        return {
            "action": "create_task",
            "reason": str(understanding.get("path_reason", "") or "brain_routed_to_task"),
            "final_decision": "continue",
            "message": "",
            "task_brief": build_default_task_brief(
                working_hypothesis=str(understanding.get("working_hypothesis", "") or ""),
                intent=str(understanding.get("intent", "") or ""),
            ),
        }

    return {
        "action": "none",
        "reason": str(understanding.get("path_reason", "") or "brain_answered_directly"),
        "final_decision": "answer",
        "message": build_companion_prompt(
            user_input="",
            emotion=emotion,
            short=True,
        ),
        "task_brief": "",
    }


def _handle_paused_task(
    *,
    user_input: str,
    task: dict[str, Any],
    emotion: str,
) -> BrainControlPacket:
    action, reason = _decide_paused_task_action(
        user_input=user_input,
        task=task,
        emotion=emotion,
    )
    if action == "resume_task":
        return {
            "action": "resume_task",
            "reason": reason,
            "task": dict(task or {}),
        }
    if action == "defer":
        return {
            "action": "defer",
            "reason": reason,
            "task": _strip_resume_payload(task),
        }
    return {
        "action": "pause_task",
        "reason": reason,
        "final_decision": "answer",
        "message": _build_paused_task_hold_message(
            user_input=user_input,
            task=task,
            reason=reason,
            emotion=emotion,
        ),
        "task": _strip_resume_payload(task),
    }


def _strip_resume_payload(task: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(task or {})
    cleaned.pop("resume_payload", None)
    return cleaned


def _decide_paused_task_action(
    *,
    user_input: str,
    task: dict[str, Any],
    emotion: str,
) -> tuple[str, str]:
    text = str(user_input or "").strip()
    resume_payload = task.get("resume_payload")
    pending_review = dict(task.get("pending_review", {}) or {})
    missing = [str(item).strip() for item in (task.get("missing", []) or []) if str(item).strip()]

    if _looks_like_pause_request(text):
        return "pause_task", "user_requested_pause"
    if resume_payload not in (None, "", [], {}):
        if pending_review:
            return "resume_task", "user_responded_to_pending_review"
        if missing:
            return "resume_task", "user_provided_missing_information"
        return "resume_task", "user_requested_resume"
    if _looks_like_resume_request(text):
        if missing:
            return "resume_task", "user_requested_resume_for_missing_information"
        if not pending_review:
            return "resume_task", "user_requested_resume"
    if _looks_like_companionship_or_explanation(text, emotion=emotion):
        return "pause_task", "brain_prioritized_companionship_or_explanation"
    if _looks_like_priority_switch(text):
        return "defer", "user_switched_priority"
    if missing:
        if _looks_like_new_task_input(text):
            return "defer", "user_started_new_topic_while_task_paused"
        if text:
            return "resume_task", "user_provided_missing_information"
    if pending_review and text:
        return "defer", "user_started_new_topic_while_review_paused"
    if text:
        return "defer", "paused_task_left_on_hold"
    return "pause_task", "paused_task_waiting"


def _looks_like_pause_request(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    exact_matches = {
        "pause",
        "wait",
        "later",
        "stop for now",
        "等等",
        "等下",
        "等一下",
        "先停一下",
        "先暂停",
        "稍等",
        "回头再说",
        "先别继续",
    }
    prefixes = ("pause", "wait", "later", "先停", "先暂停", "稍等", "等会", "回头", "先别继续")
    return lowered in exact_matches or any(lowered.startswith(prefix) for prefix in prefixes)


def _looks_like_resume_request(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower().strip()
    exact_matches = {
        "resume",
        "continue",
        "go ahead",
        "继续",
        "继续吧",
        "继续执行",
        "恢复",
        "恢复执行",
        "接着来",
        "接着做",
    }
    prefixes = (
        "resume",
        "continue",
        "go ahead",
        "继续",
        "恢复",
        "接着",
    )
    return lowered in exact_matches or any(lowered.startswith(prefix) for prefix in prefixes)


def _looks_like_new_task_input(text: str) -> bool:
    if not text:
        return False
    if _looks_like_priority_switch(text):
        return True
    return looks_task_like(text)


def _looks_like_companionship_or_explanation(text: str, *, emotion: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    emotion_keywords = (
        "难受",
        "崩溃",
        "想哭",
        "焦虑",
        "害怕",
        "不舒服",
        "烦",
        "生气",
        "委屈",
        "累",
        "陪我",
        "安慰",
        "救命",
        "help me calm",
        "anxious",
        "overwhelmed",
    )
    explanation_keywords = (
        "什么意思",
        "为什么",
        "怎么回事",
        "解释",
        "说明一下",
        "先说清楚",
        "看不懂",
        "没懂",
        "what do you mean",
        "explain",
        "why",
    )
    if any(keyword in lowered for keyword in emotion_keywords):
        return True
    if any(keyword in lowered for keyword in explanation_keywords):
        return True
    return emotion not in {"平静", "开心"} and len(text) <= 12 and any(mark in text for mark in ("?", "？"))


def _looks_like_priority_switch(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    keywords = (
        "先处理这个",
        "先看这个",
        "还有个更急",
        "更急",
        "另外一件",
        "另一个问题",
        "换个事情",
        "换个问题",
        "新任务",
        "urgent",
        "asap",
    )
    return any(keyword in lowered for keyword in keywords)


def _build_paused_task_hold_message(
    *,
    user_input: str,
    task: dict[str, Any],
    reason: str,
    emotion: str,
) -> str:
    del task, emotion
    if reason == "user_requested_pause":
        return "好，我先把刚才的执行保持暂停，不继续往下跑。你想恢复时直接跟我说‘继续’就行。"
    if reason == "brain_prioritized_companionship_or_explanation":
        lowered = str(user_input or "").lower()
        if any(token in lowered for token in ("什么意思", "解释", "为什么", "怎么回事", "explain", "why")):
            return "好，我先不继续跑刚才的执行。你现在更想让我先解释当前进展，还是你要先补充信息继续？"
        return "好，我先不继续跑刚才的执行，先陪你把现在这部分理顺。你可以直接告诉我，此刻最想先处理的是哪一点。"
    if reason == "user_switched_priority":
        return "好，我先把刚才的执行挂起，不往下推进。你现在更急的这件事，我们先把重点说清楚。"
    return "好，我先保持当前执行暂停。你想恢复时告诉我‘继续’，或直接补充新的信息也可以。"


__all__ = [
    "build_default_task_brief",
    "handle_user_turn",
    "looks_task_like",
]
