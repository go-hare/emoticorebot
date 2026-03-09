"""Main-brain node for orchestration."""

from __future__ import annotations

from typing import Any

from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.core.state import (
    ExecutorState,
    MainBrainDeliberationPacket,
    MainBrainFinalizePacket,
    MainBrainState,
    OrchestrationState,
)

MAX_LOOP_ROUNDS = 3


async def main_brain_node(state: OrchestrationState, runtime) -> OrchestrationState:
    """Decide whether to use the executor and produce the user-facing result."""
    main_brain: MainBrainState = state["main_brain"]
    executor: ExecutorState = state["executor"]
    user_input = state["user_input"]
    dialogue_history = state.get("dialogue_history", [])
    metadata = state.get("metadata", {}) or {}
    loop_count = int(state.get("loop_count", 0) or 0)

    if not _has_executor_packet(executor):
        deliberation: MainBrainDeliberationPacket = await runtime.main_brain_deliberate(
            user_input=user_input,
            dialogue_history=dialogue_history,
            emotion=main_brain.emotion,
            pad=main_brain.pad,
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
            session_id=state.get("session_id", ""),
        )
        main_brain.intent = deliberation.get("intent", "")
        main_brain.working_hypothesis = deliberation.get("working_hypothesis", "")
        main_brain.question_to_executor = deliberation.get("question_to_executor", "")
        main_brain.model_name = str(deliberation.get("model_name", "") or "")
        main_brain.prompt_tokens = int(deliberation.get("prompt_tokens", 0) or 0)
        main_brain.completion_tokens = int(deliberation.get("completion_tokens", 0) or 0)
        main_brain.total_tokens = int(deliberation.get("total_tokens", 0) or 0)

        if deliberation.get("need_executor"):
            question = main_brain.question_to_executor or _build_default_executor_question(main_brain)
            state["metadata"] = _merge_followup_metadata(metadata)
            _queue_executor_question(executor, question)
            state["loop_count"] = loop_count + 1
            state["done"] = False
            return state

        main_brain.final_decision = "answer"
        main_brain.final_message = deliberation.get("final_message", "") or build_companion_prompt(main_brain.emotion)
        state["output"] = main_brain.final_message
        state["done"] = True
        return state

    finalize: MainBrainFinalizePacket = await runtime.main_brain_finalize(
        user_input=user_input,
        history=dialogue_history,
        emotion=main_brain.emotion,
        pad=main_brain.pad,
        main_brain_intent=main_brain.intent,
        main_brain_working_hypothesis=main_brain.working_hypothesis,
        executor_summary=runtime._build_executor_summary({"executor": executor, "main_brain": main_brain}),
        executor_status=executor.status,
        executor_missing_params=list(executor.missing_params),
        executor_recommended_action=executor.recommended_action,
        loop_count=loop_count,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        session_id=state.get("session_id", ""),
    )

    main_brain.final_decision = str(finalize.get("decision", "") or "")
    main_brain.final_message = str(finalize.get("message", "") or "")
    main_brain.question_to_executor = str(finalize.get("question_to_executor", "") or "")
    main_brain.model_name = str(finalize.get("model_name", "") or "")
    main_brain.prompt_tokens = int(finalize.get("prompt_tokens", 0) or 0)
    main_brain.completion_tokens = int(finalize.get("completion_tokens", 0) or 0)
    main_brain.total_tokens = int(finalize.get("total_tokens", 0) or 0)

    if main_brain.final_decision == "continue_deliberation":
        if loop_count >= MAX_LOOP_ROUNDS:
            main_brain.final_decision, main_brain.final_message = _force_complete(executor)
            state["output"] = main_brain.final_message
            state["done"] = True
            return state

        question = main_brain.question_to_executor or _build_followup_executor_question(executor)
        state["metadata"] = _merge_followup_metadata(metadata)
        _queue_executor_question(executor, question)
        state["loop_count"] = loop_count + 1
        state["done"] = False
        return state

    if main_brain.final_decision == "ask_user":
        state["output"] = main_brain.final_message or build_missing_info_prompt(executor.missing_params)
    else:
        state["output"] = main_brain.final_message or executor.analysis or "我先给你一个当前能确认的结论，我们可以继续往下推进。"
        main_brain.final_decision = "answer"

    state["done"] = True
    return state


def _has_executor_packet(executor: ExecutorState) -> bool:
    if executor.status in {"completed", "needs_input", "uncertain", "failed"}:
        return True
    return bool(executor.analysis or executor.attempts)


def _merge_followup_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {**metadata, "intent_params": {}}


def _queue_executor_question(executor: ExecutorState, question: str) -> None:
    executor.request = question
    executor.status = "queued"
    executor.analysis = ""
    executor.risks = []
    executor.recommended_action = ""
    executor.confidence = 0.0
    executor.missing_params = []


def _build_default_executor_question(main_brain: MainBrainState) -> str:
    if main_brain.working_hypothesis:
        return (
            "Analyze the current working hypothesis, identify evidence, risks, "
            f"and the best next action: {main_brain.working_hypothesis}"
        )
    return "Analyze the current internal question and return evidence, risks, and the best next action."


def _build_followup_executor_question(executor: ExecutorState) -> str:
    if executor.risks:
        risk_text = "; ".join(executor.risks[:2])
        return f"Focus on these key risks and produce a more robust next step: {risk_text}"
    if executor.analysis:
        return f"Strengthen the weakest part of this analysis and make the next action clearer: {executor.analysis}"
    return "Fill the most important evidence gaps and provide the next action."


def _force_complete(executor: ExecutorState) -> tuple[str, str]:
    if executor.status == "needs_input" or executor.missing_params:
        return "ask_user", build_missing_info_prompt(executor.missing_params)
    if executor.analysis:
        return "answer", executor.analysis
    return "answer", "我先给你一个阶段性结论，不过还需要更多信息才能更稳。"
