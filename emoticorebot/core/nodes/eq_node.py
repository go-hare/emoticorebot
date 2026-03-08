"""EQ Node - EQ 主导的内部讨论与最终用户输出。"""

from __future__ import annotations

from typing import Any

from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.core.state import (
    EQDeliberationPacket,
    EQFinalizePacket,
    EQState,
    FusionState,
    IQState,
)

MAX_DISCUSSION_ROUNDS = 3


async def eq_node(state: FusionState, runtime) -> FusionState:
    """EQ 节点：判断是否征询 IQ，并负责最终对外表达。"""
    eq: EQState = state["eq"]
    iq: IQState = state["iq"]
    user_input = state["user_input"]
    user_eq_history = state.get("user_eq_history", [])
    metadata = state.get("metadata", {}) or {}
    discussion_count = int(state.get("discussion_count", 0) or 0)

    if not _has_iq_packet(iq):
        deliberation: EQDeliberationPacket = await runtime.eq_deliberate(
            user_input=user_input,
            user_eq_history=user_eq_history,
            emotion=eq.emotion,
            pad=eq.pad,
        )
        eq.intent = deliberation.get("intent", "")
        eq.working_hypothesis = deliberation.get("working_hypothesis", "")
        eq.question_to_iq = deliberation.get("question_to_iq", "")
        eq.model_name = str(deliberation.get("model_name", "") or "")
        eq.prompt_tokens = int(deliberation.get("prompt_tokens", 0) or 0)
        eq.completion_tokens = int(deliberation.get("completion_tokens", 0) or 0)
        eq.total_tokens = int(deliberation.get("total_tokens", 0) or 0)

        if deliberation.get("need_iq"):
            question = eq.question_to_iq or _build_default_iq_question(eq)
            state["metadata"] = _merge_followup_metadata(metadata)
            _queue_iq_question(iq, question)
            state["discussion_count"] = discussion_count + 1
            state["done"] = False
            return state

        eq.final_decision = "answer"
        eq.final_message = deliberation.get("final_message", "") or build_companion_prompt(eq.emotion)
        state["output"] = eq.final_message
        state["done"] = True
        return state

    finalize: EQFinalizePacket = await runtime.eq_finalize(
        user_input=user_input,
        history=user_eq_history,
        emotion=eq.emotion,
        pad=eq.pad,
        eq_intent=eq.intent,
        eq_working_hypothesis=eq.working_hypothesis,
        iq_summary=runtime._build_iq_summary({"iq": iq, "eq": eq}),
        iq_status=iq.status,
        iq_missing_params=list(iq.missing_params),
        iq_recommended_action=iq.recommended_action,
        discussion_count=discussion_count,
    )

    eq.final_decision = str(finalize.get("decision", "") or "")
    eq.final_message = str(finalize.get("message", "") or "")
    eq.question_to_iq = str(finalize.get("question_to_iq", "") or "")
    eq.model_name = str(finalize.get("model_name", "") or "")
    eq.prompt_tokens = int(finalize.get("prompt_tokens", 0) or 0)
    eq.completion_tokens = int(finalize.get("completion_tokens", 0) or 0)
    eq.total_tokens = int(finalize.get("total_tokens", 0) or 0)

    if eq.final_decision == "continue_deliberation":
        if discussion_count >= MAX_DISCUSSION_ROUNDS:
            eq.final_decision, eq.final_message = _force_complete(iq)
            state["output"] = eq.final_message
            state["done"] = True
            return state

        question = eq.question_to_iq or _build_followup_iq_question(iq)
        state["metadata"] = _merge_followup_metadata(metadata)
        _queue_iq_question(iq, question)
        state["discussion_count"] = discussion_count + 1
        state["done"] = False
        return state

    if eq.final_decision == "ask_user":
        state["output"] = eq.final_message or build_missing_info_prompt(iq.missing_params)
    else:
        state["output"] = eq.final_message or iq.analysis or "嗯，我把这件事捋顺了，我们接着来。"
        eq.final_decision = "answer"

    state["done"] = True
    return state


def _has_iq_packet(iq: IQState) -> bool:
    if iq.status in {"completed", "needs_input", "uncertain", "failed"}:
        return True
    return bool(iq.analysis or iq.attempts)


def _merge_followup_metadata(
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {**metadata, "intent_params": {}}


def _queue_iq_question(iq: IQState, question: str) -> None:
    iq.request = question
    iq.status = "queued"
    iq.analysis = ""
    iq.risks = []
    iq.recommended_action = ""
    iq.confidence = 0.0
    iq.missing_params = []


def _build_default_iq_question(eq: EQState) -> str:
    if eq.working_hypothesis:
        return f"请围绕这个判断做理性分析：{eq.working_hypothesis}。并给我证据、风险和建议动作。"
    return "请分析当前内部问题的可执行性，并给我证据、风险和建议动作。"


def _build_followup_iq_question(iq: IQState) -> str:
    if iq.risks:
        risk_text = "；".join(iq.risks[:2])
        return f"请围绕这些风险继续补充最关键的判断依据，并给我更稳妥的下一步建议：{risk_text}"
    if iq.analysis:
        return f"请针对这段分析补强最薄弱的部分，并给我更明确建议：{iq.analysis}"
    return "请补充最关键的证据、风险和建议动作，帮助我完成最终判断。"


def _force_complete(iq: IQState) -> tuple[str, str]:
    if iq.status == "needs_input" or iq.missing_params:
        return "ask_user", build_missing_info_prompt(iq.missing_params)
    if iq.analysis:
        return "answer", iq.analysis
    return "answer", "我先给你一个当前能确认的结论：信息还不够完整，但我会继续帮你推进。"
