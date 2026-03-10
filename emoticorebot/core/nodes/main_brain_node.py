"""Main-brain node for the turn graph."""

from __future__ import annotations

from typing import Any

from emoticorebot.core.reply_utils import build_missing_info_prompt
from emoticorebot.core.state import (
    ExecutorState,
    MainBrainDeliberationPacket,
    MainBrainFinalizePacket,
    MainBrainState,
    TurnState,
)

MAX_LOOP_ROUNDS = 3


async def main_brain_node(state: TurnState, runtime) -> TurnState:
    """Decide whether to use the executor and produce the user-facing result."""
    main_brain: MainBrainState = state["main_brain"]
    executor: ExecutorState = state["executor"]
    user_input = state["user_input"]
    dialogue_history = state.get("dialogue_history", [])
    metadata = state.get("metadata", {}) or {}
    loop_count = int(state.get("loop_count", 0) or 0)

    main_brain.execution_action = ""
    main_brain.execution_reason = ""

    paused_execution = _extract_paused_execution(metadata)
    if paused_execution and not _has_executor_packet(executor):
        control = runtime.main_brain_decide_paused_execution(
            user_input=user_input,
            execution=paused_execution,
            emotion=main_brain.emotion,
        )
        main_brain.execution_action = str(control.get("action", "") or "")
        main_brain.execution_reason = str(control.get("reason", "") or "")
        if main_brain.execution_action == "resume":
            state["metadata"] = _set_execution_metadata(
                metadata,
                dict(control.get("execution", {}) or paused_execution),
            )
            _queue_executor_resume(executor, user_input=user_input, execution=paused_execution)
            state["done"] = False
            return state

        state["metadata"] = _set_paused_execution_metadata(
            metadata,
            dict(control.get("execution", {}) or paused_execution),
        )
        if main_brain.execution_action == "defer":
            metadata = state["metadata"]
        else:
            main_brain.final_decision = str(control.get("final_decision", "answer") or "answer")
            main_brain.final_message = str(control.get("message", "") or "")
            state["output"] = main_brain.final_message
            state["done"] = True
            return state

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

        control = runtime.main_brain_control_after_deliberation(
            deliberation=deliberation,
            emotion=main_brain.emotion,
        )
        main_brain.execution_action = str(control.get("action", "") or "")
        main_brain.execution_reason = str(control.get("reason", "") or "")

        if main_brain.execution_action == "start":
            question = str(control.get("question_to_executor", "") or main_brain.question_to_executor or "")
            main_brain.question_to_executor = question
            state["metadata"] = _merge_followup_metadata(metadata)
            _queue_executor_question(executor, question)
            state["loop_count"] = loop_count + 1
            state["done"] = False
            return state

        main_brain.final_decision = str(control.get("final_decision", "answer") or "answer")
        main_brain.final_message = str(control.get("message", "") or "")
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
        executor_missing=list(executor.missing),
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

    control = runtime.main_brain_control_after_finalize(
        finalize=finalize,
        loop_count=loop_count,
        max_loop_rounds=MAX_LOOP_ROUNDS,
        executor_control_state=executor.control_state,
        executor_status=executor.status,
        executor_missing=list(executor.missing),
        executor_analysis=executor.analysis,
        executor_risks=list(executor.risks),
    )
    main_brain.execution_action = str(control.get("action", "") or "")
    main_brain.execution_reason = str(control.get("reason", "") or "")
    main_brain.final_decision = str(control.get("final_decision", main_brain.final_decision) or main_brain.final_decision)
    main_brain.final_message = str(control.get("message", main_brain.final_message) or main_brain.final_message)
    main_brain.question_to_executor = str(control.get("question_to_executor", main_brain.question_to_executor) or main_brain.question_to_executor)

    if main_brain.execution_action == "continue":
        question = main_brain.question_to_executor
        state["metadata"] = _merge_followup_metadata(metadata)
        _queue_executor_question(executor, question)
        state["loop_count"] = loop_count + 1
        state["done"] = False
        return state

    if main_brain.final_decision == "ask_user":
        state["output"] = main_brain.final_message or build_missing_info_prompt(executor.missing)
    else:
        state["output"] = main_brain.final_message or executor.analysis or "我先给你一个当前能确认的结论，我们可以继续往下推进。"
        main_brain.final_decision = "answer"

    state["done"] = True
    return state


def _has_executor_packet(executor: ExecutorState) -> bool:
    if executor.status in {"done", "need_more", "failed"}:
        return True
    return bool(executor.analysis or executor.attempts)


def _extract_paused_execution(metadata: dict[str, Any]) -> dict[str, Any]:
    for key in ("paused_execution", "execution"):
        execution = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
        if str((execution or {}).get("control_state", "") or "").strip() == "paused":
            return dict(execution)
    return {}


def _merge_followup_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged.pop("paused_execution", None)
    merged["execution"] = {}
    return merged


def _set_execution_metadata(metadata: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated.pop("paused_execution", None)
    updated["execution"] = dict(execution or {})
    return updated


def _set_paused_execution_metadata(metadata: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated.pop("execution", None)
    if execution:
        updated["paused_execution"] = dict(execution)
    else:
        updated.pop("paused_execution", None)
    return updated


def _queue_executor_question(executor: ExecutorState, question: str) -> None:
    executor.request = question
    executor.control_state = "running"
    executor.status = "none"
    executor.analysis = ""
    executor.risks = []
    executor.recommended_action = ""
    executor.confidence = 0.0
    executor.missing = []
    executor.pending_review = {}


def _queue_executor_resume(executor: ExecutorState, *, user_input: str, execution: dict[str, Any]) -> None:
    request = str(user_input or "").strip()
    if _is_plain_resume_signal(request):
        request = ""
    executor.request = str(request or execution.get("summary", "") or "继续上次执行").strip()
    executor.thread_id = str(execution.get("thread_id", "") or "")
    executor.run_id = str(execution.get("run_id", "") or "")
    executor.control_state = "running"
    executor.status = "none"
    executor.analysis = ""
    executor.risks = []
    executor.recommended_action = ""
    executor.confidence = 0.0
    executor.missing = [
        str(item).strip()
        for item in (execution.get("missing", []) or [])
        if str(item).strip()
    ]
    executor.pending_review = dict(execution.get("pending_review", {}) or {})


def _is_plain_resume_signal(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return lowered in {
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
