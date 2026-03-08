"""EQ Node - EQ 主导的内部讨论与最终用户输出。"""

from __future__ import annotations

from typing import Any

from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.core.state import EQState, FusionState, IQState


MAX_DISCUSSION_ROUNDS = 3


async def eq_node(state: FusionState, runtime) -> FusionState:
    """EQ 节点：先主导判断，再整合 IQ 的内部参谋意见。"""
    eq: EQState = state["eq"]
    iq: IQState = state["iq"]
    user_input = state["user_input"]
    user_eq_history = state.get("user_eq_history", [])
    metadata = state.get("metadata", {}) or {}
    pending_task = metadata.get("pending_task") if isinstance(metadata.get("pending_task"), dict) else None
    current_task = metadata.get("current_task") if isinstance(metadata.get("current_task"), dict) else None
    internal_iq_summaries = metadata.get("recent_iq_summaries") if isinstance(metadata.get("recent_iq_summaries"), list) else []
    discussion_count = int(state.get("discussion_count", 0) or 0)

    if not _has_iq_packet(iq):
        deliberation = await runtime.eq_deliberate(
            user_input=user_input,
            user_eq_history=user_eq_history,
            emotion=eq.emotion,
            pad=eq.pad,
            pending_task=pending_task,
            current_task=current_task,
            internal_iq_summaries=internal_iq_summaries,
        )
        eq.intent = deliberation.get("intent", "")
        eq.emotional_goal = deliberation.get("emotional_goal", "")
        eq.working_hypothesis = deliberation.get("working_hypothesis", "")
        eq.question_to_iq = deliberation.get("question_to_iq", "")
        eq.selected_experts = list(deliberation.get("selected_experts", []) or [])
        eq.expert_questions = dict(deliberation.get("expert_questions", {}) or {})
        eq.task_continuity = str(deliberation.get("task_continuity", "") or "")
        eq.task_label = str(deliberation.get("task_label", "") or "")
        eq.reason = deliberation.get("reason", "")

        if deliberation.get("need_iq"):
            question = eq.question_to_iq or _build_default_iq_question(eq, pending_task)
            metadata = _merge_followup_metadata(
                metadata,
                pending_task,
                selected_experts=eq.selected_experts,
                expert_questions=eq.expert_questions,
                accepted_experts=eq.accepted_experts,
                rejected_experts=eq.rejected_experts,
                arbitration_summary=eq.arbitration_summary,
            )
            state["metadata"] = metadata
            _queue_iq_question(iq, question)
            state["discussion_count"] = discussion_count + 1
            state["done"] = False
            return state

        eq.final_decision = deliberation.get("final_decision", "") or "answer"
        eq.final_message = deliberation.get("final_message", "") or build_companion_prompt(eq.emotion)
        state["output"] = eq.final_message
        state["done"] = True
        return state

    finalize = await runtime.eq_finalize(
        user_input=user_input,
        history=user_eq_history,
        emotion=eq.emotion,
        pad=eq.pad,
        pending_task=pending_task,
        current_task=current_task,
        internal_iq_summaries=internal_iq_summaries,
        eq_intent=eq.intent,
        eq_emotional_goal=eq.emotional_goal,
        eq_working_hypothesis=eq.working_hypothesis,
        iq_summary=runtime._build_iq_summary({"iq": iq, "eq": eq}),
        iq_status=iq.status,
        iq_missing_params=list(iq.missing_params),
        iq_recommended_action=iq.recommended_action,
        iq_selected_experts=list(iq.selected_experts),
        discussion_count=discussion_count,
    )

    eq.final_decision = finalize.get("decision", "")
    eq.final_message = finalize.get("message", "")
    eq.question_to_iq = finalize.get("question_to_iq", "")
    eq.selected_experts = list(finalize.get("selected_experts", []) or [])
    eq.expert_questions = dict(finalize.get("expert_questions", {}) or {})
    eq.accepted_experts = list(finalize.get("accepted_experts", []) or [])
    eq.rejected_experts = list(finalize.get("rejected_experts", []) or [])
    eq.arbitration_summary = str(finalize.get("arbitration_summary", "") or "")
    eq.task_continuity = str(finalize.get("task_continuity", "") or eq.task_continuity or "")
    eq.task_label = str(finalize.get("task_label", "") or eq.task_label or "")
    eq.reason = finalize.get("reason", "")
    _append_eq_arbitration_event(state, eq, iq)

    if eq.final_decision == "continue_deliberation":
        if discussion_count >= MAX_DISCUSSION_ROUNDS:
            eq.final_decision, eq.final_message = _force_complete(iq)
            state["output"] = eq.final_message
            state["done"] = True
            return state

        question = eq.question_to_iq or _build_followup_iq_question(iq)
        metadata = _merge_followup_metadata(
            metadata,
            pending_task,
            selected_experts=eq.selected_experts,
            expert_questions=eq.expert_questions,
            accepted_experts=eq.accepted_experts,
            rejected_experts=eq.rejected_experts,
            arbitration_summary=eq.arbitration_summary,
        )
        state["metadata"] = metadata
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
    return bool(iq.analysis or iq.error or iq.attempts)


def _merge_followup_metadata(
    metadata: dict[str, Any],
    pending_task: dict[str, Any] | None,
    *,
    selected_experts: list[str] | None = None,
    expert_questions: dict[str, str] | None = None,
    accepted_experts: list[str] | None = None,
    rejected_experts: list[str] | None = None,
    arbitration_summary: str = "",
) -> dict[str, Any]:
    base_params = metadata.get("intent_params") if isinstance(metadata.get("intent_params"), dict) else {}
    merged_params = {
        **base_params,
        "selected_experts": list(selected_experts or base_params.get("selected_experts") or []),
        "expert_questions": dict(expert_questions or base_params.get("expert_questions") or {}),
        "eq_accepted_experts": list(accepted_experts or base_params.get("eq_accepted_experts") or []),
        "eq_rejected_experts": list(rejected_experts or base_params.get("eq_rejected_experts") or []),
        "eq_arbitration_summary": arbitration_summary or str(base_params.get("eq_arbitration_summary", "") or ""),
    }
    if not pending_task or not pending_task.get("task"):
        return {
            **metadata,
            "intent_params": merged_params,
        }
    return {
        **metadata,
        "intent_params": {
            **merged_params,
            "missing_params": list(pending_task.get("missing_params") or []),
            "resume_task": str(pending_task.get("task", "") or ""),
        },
    }


def _queue_iq_question(iq: IQState, question: str) -> None:
    iq.task = question
    iq.status = "queued"
    iq.analysis = ""
    iq.evidence = []
    iq.risks = []
    iq.options = []
    iq.recommended_action = ""
    iq.confidence = 0.0
    iq.rationale_summary = ""
    iq.missing_params = []
    iq.tool_calls = []
    iq.error = ""
    iq.iterations = 0


def _build_default_iq_question(
    eq: EQState,
    pending_task: dict[str, Any] | None,
) -> str:
    if pending_task and pending_task.get("task"):
        task = str(pending_task.get("task", "") or "").strip()
        return f"请继续这个任务：{task}。并告诉我还缺什么、风险是什么、建议我怎么回复。"
    if eq.working_hypothesis:
        return f"请围绕这个判断做理性分析：{eq.working_hypothesis}。并给我证据、风险和建议动作。"
    return "请分析当前内部任务的可执行性，并给我证据、风险和建议动作。"


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
    if iq.error:
        return "answer", f"我现在能确定的是：{iq.error}"
    return "answer", "我先给你一个当前能确认的结论：信息还不够完整，但我会继续帮你推进。"


def _append_eq_arbitration_event(state: FusionState, eq: EQState, iq: IQState) -> None:
    metadata = state.get("metadata", {}) or {}
    task_id = str(metadata.get("task_id", "") or "").strip()
    content = str(eq.arbitration_summary or eq.reason or eq.final_decision or "").strip()
    event = {
        "role": "assistant",
        "phase": "eq_arbitration",
        "task_id": task_id,
        "task": str(iq.task or "").strip(),
        "task_continuity": str(eq.task_continuity or "").strip(),
        "task_label": str(eq.task_label or "").strip(),
        "content": content,
        "final_decision": str(eq.final_decision or "").strip(),
        "accepted_experts": list(eq.accepted_experts or []),
        "rejected_experts": list(eq.rejected_experts or []),
        "selected_experts": list(eq.selected_experts or []),
        "arbitration_summary": str(eq.arbitration_summary or "").strip(),
    }
    eq_iq_history = list(state.get("eq_iq_history", []) or [])
    eq_iq_history.append(event)
    state["eq_iq_history"] = eq_iq_history
