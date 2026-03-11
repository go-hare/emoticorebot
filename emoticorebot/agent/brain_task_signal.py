"""Task-signal handling for the brain layer."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from emoticorebot.agent.brain_shared import compact_text, extract_json_string_field, parse_json_dict
from emoticorebot.agent.reply_utils import build_missing_info_prompt
from emoticorebot.agent.state import BrainControlPacket, BrainFinalizePacket
from emoticorebot.runtime.event_bus import TaskSignal

if TYPE_CHECKING:
    from emoticorebot.agent.brain import BrainService


async def handle_task_signal(
    service: "BrainService",
    *,
    signal: TaskSignal,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    brain_intent: str,
    brain_working_hypothesis: str,
    loop_count: int,
    max_loop_rounds: int,
    task: dict[str, Any] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> BrainControlPacket:
    task_packet = dict(task or {})
    if not _is_result_signal(signal=signal, task=task_packet):
        if not _should_notify_user(signal):
            return {
                "action": "none",
                "reason": "task_signal_recorded",
            }
        return {
            "action": "none",
            "reason": "brain_shared_task_progress",
            "message": _build_progress_message(signal),
            "notify_user": True,
        }

    finalize = await _finalize_from_task(
        service,
        user_input=user_input,
        history=history,
        emotion=emotion,
        pad=pad,
        brain_intent=brain_intent,
        brain_working_hypothesis=brain_working_hypothesis,
        task=task_packet,
        loop_count=loop_count,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
    )
    control = _control_after_finalize(
        finalize=finalize,
        loop_count=loop_count,
        max_loop_rounds=max_loop_rounds,
        task_control_state=str(task_packet.get("control_state", "") or ""),
        task_status=str(task_packet.get("status", "") or ""),
        task_missing=[str(item).strip() for item in list(task_packet.get("missing", []) or []) if str(item).strip()],
        task_analysis=str(task_packet.get("analysis", "") or task_packet.get("summary", "") or ""),
        task_risks=[str(item).strip() for item in list(task_packet.get("risks", []) or []) if str(item).strip()],
    )
    control["retrieval_query"] = str(finalize.get("retrieval_query", "") or user_input)
    control["retrieval_focus"] = [
        str(item).strip()
        for item in list(finalize.get("retrieval_focus", []) or [])
        if str(item).strip()
    ]
    control["retrieved_memory_ids"] = [
        str(item).strip()
        for item in list(finalize.get("retrieved_memory_ids", []) or [])
        if str(item).strip()
    ]
    control["model_name"] = str(finalize.get("model_name", "") or "")
    control["prompt_tokens"] = int(finalize.get("prompt_tokens", 0) or 0)
    control["completion_tokens"] = int(finalize.get("completion_tokens", 0) or 0)
    control["total_tokens"] = int(finalize.get("total_tokens", 0) or 0)
    return control


def _is_result_signal(*, signal: TaskSignal, task: dict[str, Any]) -> bool:
    event = str(signal.event or "").strip().lower()
    if event == "task.result":
        return True
    if task and str(task.get("status", "") or "").strip() in {"done", "need_more", "failed"}:
        return True
    payload = dict(signal.payload or {})
    return any(key in payload for key in ("analysis", "status", "control_state", "recommended_action"))


def _should_notify_user(signal: TaskSignal) -> bool:
    event = str(signal.event or "").strip().lower()
    payload = dict(signal.payload or {})
    if bool(payload.get("requires_attention", False)):
        return True
    if event in {"task.blocked", "task.warning", "task.error"}:
        return True
    return False


def _build_progress_message(signal: TaskSignal) -> str:
    content = str(signal.content or "").strip()
    if content:
        return content
    payload = dict(signal.payload or {})
    stage = str(payload.get("stage", "") or payload.get("node", "") or "").strip()
    action = str(payload.get("action", "") or payload.get("result", "") or "").strip()
    if stage and action:
        return f"当前进展：{stage}，{action}"
    if stage:
        return f"当前进展：{stage}"
    return "我这边收到新的任务阶段更新了，正在继续推进。"


async def _finalize_from_task(
    service: "BrainService",
    *,
    user_input: str,
    history: list[dict[str, Any]],
    emotion: str,
    pad: dict[str, float],
    brain_intent: str,
    brain_working_hypothesis: str,
    task: dict[str, Any],
    loop_count: int,
    channel: str,
    chat_id: str,
    session_id: str,
) -> BrainFinalizePacket:
    task_summary = str(task.get("analysis", "") or task.get("summary", "") or "").strip()
    task_status = str(task.get("status", "") or "").strip()
    task_missing = [str(item).strip() for item in list(task.get("missing", []) or []) if str(item).strip()]
    task_recommended_action = str(task.get("recommended_action", "") or "").strip()
    prompt = f"""
你是 `brain`，正在读取本轮 `central` 结果并做最终决策。

请综合：
- 你的初始判断
- 当前 `central` 返回结果
- 用户真实需求

然后只在以下三种决策中选择一种：
- `answer`：已经可以直接对用户回复
- `ask_user`：必须让用户补充信息
- `continue`：还需要继续让 `central` 往下执行

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `final_decision`：只能是 `answer`、`ask_user`、`continue`。
- `final_message`：当决策为 `answer` 或 `ask_user` 时，要给用户看的话；必须使用与用户相同的语言。
- `task_brief`：当决策为 `continue` 时，发给 `central` 的下一条内部问题。

硬性规则：
1. 如果 `final_decision` = `continue`：
   - `task_brief` 必须非空
   - `final_message` 必须是空字符串 `""`
2. 如果 `final_decision` = `answer` 或 `ask_user`：
   - `final_message` 必须非空
   - `task_brief` 必须是空字符串 `""`
3. 不要遗漏任何字段。

标准结构：
{{
  "final_decision": "answer|ask_user|continue",
  "final_message": "...",
  "task_brief": "..."
}}

用户输入：{user_input}
主脑意图：{brain_intent or '（空）'}
主脑工作假设：{compact_text(brain_working_hypothesis, limit=140) or '（空）'}
central 摘要：{compact_text(task_summary, limit=320) or '（空）'}
当前循环次数：{loop_count}
""".strip()

    raw_text, metrics = await service._run_brain_task(
        history=history,
        current_message=prompt,
        current_emotion=emotion,
        pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        internal_task_summaries=[task_summary] if task_summary else None,
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
        query=(f"{user_input}\n{task_summary}".strip()),
        retrieval_focus=["user", "goal", "constraint", "tool", "skill"],
    )

    parsed = parse_json_dict(raw_text)
    if parsed is None:
        recovered = _recover_finalize(raw_text)
        if recovered is not None:
            recovered.update(metrics)
            return recovered
        fallback = _fallback_finalize(
            task_status=task_status,
            task_analysis=task_summary,
            task_missing=task_missing,
            task_recommended_action=task_recommended_action,
        )
        fallback.update(metrics)
        return fallback

    normalized = _normalize_finalize_payload(parsed)
    if normalized is None:
        fallback = _fallback_finalize(
            task_status=task_status,
            task_analysis=task_summary,
            task_missing=task_missing,
            task_recommended_action=task_recommended_action,
        )
        fallback.update(metrics)
        return fallback
    normalized.update(metrics)
    return normalized


def _normalize_finalize_payload(parsed: dict[str, Any]) -> BrainFinalizePacket | None:
    if not isinstance(parsed, dict):
        return None

    decision = str(parsed.get("final_decision", "") or parsed.get("decision", "") or "").strip().lower()
    if decision not in {"answer", "ask_user", "continue"}:
        return None

    message = str(parsed.get("final_message", "") or parsed.get("message", "") or "").strip()
    task_brief = str(parsed.get("task_brief", "") or "").strip()
    if decision == "continue" and not task_brief:
        task_brief = message
    if decision != "continue":
        task_brief = ""
    if decision in {"answer", "ask_user"} and not message:
        return None

    return {
        "final_decision": decision,
        "final_message": message,
        "decision": decision,
        "message": message,
        "task_brief": task_brief,
    }


def _recover_finalize(raw: str) -> BrainFinalizePacket | None:
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    decision = (
        extract_json_string_field(cleaned, "final_decision")
        or extract_json_string_field(cleaned, "decision")
        or "answer"
    )
    message = extract_json_string_field(cleaned, "final_message") or extract_json_string_field(cleaned, "message")
    task_brief = extract_json_string_field(cleaned, "task_brief")
    if decision not in {"answer", "ask_user", "continue"}:
        decision = "answer"
    if decision == "continue":
        if not task_brief:
            return None
        return {
            "final_decision": decision,
            "final_message": "",
            "decision": decision,
            "message": "",
            "task_brief": task_brief,
        }
    if not message:
        return None
    return {
        "final_decision": decision,
        "final_message": message,
        "decision": decision,
        "message": message,
        "task_brief": "",
    }


def _fallback_finalize(
    *,
    task_status: str,
    task_analysis: str,
    task_missing: list[str],
    task_recommended_action: str,
) -> BrainFinalizePacket:
    if task_missing or task_status == "need_more" or task_recommended_action == "ask_user":
        prompt = build_missing_info_prompt(task_missing)
        return {
            "final_decision": "ask_user",
            "final_message": prompt,
            "decision": "ask_user",
            "message": prompt,
            "task_brief": "",
        }
    if task_recommended_action == "continue_task":
        return {
            "final_decision": "continue",
            "final_message": "",
            "decision": "continue",
            "message": "",
            "task_brief": "请补上最关键的证据缺口、主要风险，以及最稳妥的下一步。",
        }
    return {
        "final_decision": "answer",
        "final_message": task_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
        "decision": "answer",
        "message": task_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
        "task_brief": "",
    }


def _control_after_finalize(
    *,
    finalize: BrainFinalizePacket,
    loop_count: int,
    max_loop_rounds: int,
    task_control_state: str,
    task_status: str,
    task_missing: list[str],
    task_analysis: str,
    task_risks: list[str],
) -> BrainControlPacket:
    decision = str(finalize.get("final_decision", "") or finalize.get("decision", "") or "answer").strip().lower()
    message = str(finalize.get("final_message", "") or finalize.get("message", "") or "").strip()
    task_brief = str(finalize.get("task_brief", "") or "").strip()

    if decision == "continue":
        if loop_count >= max_loop_rounds:
            forced_decision, forced_message = _force_complete(
                task_status=task_status,
                task_missing=task_missing,
                task_analysis=task_analysis,
            )
            return {
                "action": "pause_task" if task_control_state == "paused" else "none",
                "reason": "loop_limit_reached",
                "final_decision": forced_decision,
                "message": forced_message,
            }
        return {
            "action": "continue_task",
            "reason": "brain_requested_task_followup",
            "final_decision": "continue",
            "task_brief": task_brief or _build_followup_task_brief(task_risks=task_risks, task_analysis=task_analysis),
        }

    if decision == "ask_user":
        return {
            "action": "pause_task" if task_control_state == "paused" else "none",
            "reason": "task_waiting_for_user_input" if task_control_state == "paused" else "brain_requested_user_input",
            "final_decision": "ask_user",
            "message": message or build_missing_info_prompt(task_missing),
        }

    return {
        "action": "none",
        "reason": "task_result_finalized" if task_control_state == "completed" else "brain_answered_from_task",
        "final_decision": "answer",
        "message": message or task_analysis or "我先给你一个当前能确认的结论，我们可以继续往下推进。",
    }


def _build_followup_task_brief(*, task_risks: list[str], task_analysis: str) -> str:
    if task_risks:
        risk_text = "; ".join(str(item).strip() for item in task_risks[:2] if str(item).strip())
        if risk_text:
            return f"Focus on these key risks and produce a more robust next step: {risk_text}"
    if task_analysis:
        return f"Strengthen the weakest part of this analysis and make the next action clearer: {task_analysis}"
    return "Fill the most important evidence gaps and provide the next action."


def _force_complete(*, task_status: str, task_missing: list[str], task_analysis: str) -> tuple[str, str]:
    if task_status == "need_more" or task_missing:
        return "ask_user", build_missing_info_prompt(task_missing)
    if task_analysis:
        return "answer", task_analysis
    return "answer", "我先给你一个阶段性结论，不过还需要更多信息才能更稳。"


__all__ = ["handle_task_signal"]
