"""Explicit turn scheduler for the brain -> central loop."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.agent.reply_utils import build_missing_info_prompt
from emoticorebot.agent.state import (
    TaskState,
    BrainDeliberationPacket,
    BrainFinalizePacket,
    BrainState,
    TurnState,
    create_turn_state,
)
from emoticorebot.utils.llm_utils import json_text_block

MAX_TURN_STEPS = 8
MAX_LOOP_ROUNDS = 3


def _task_request_ready(state: TurnState) -> bool:
    task = state.get("task")
    if task is None:
        return False
    request = str(getattr(task, "request", "") or "").strip()
    control_state = str(getattr(task, "control_state", "") or "").strip()
    metadata = state.get("metadata", {}) or {}
    task_meta = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
    return bool(request or task_meta) and control_state == "running"


async def run_turn_engine(
    user_input: str,
    workspace: Path,
    runtime,
    dialogue_history: list[dict[str, Any]] | None = None,
    internal_history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
    media: list[str] | None = None,
    on_progress=None,
) -> tuple[str, TurnState]:
    """Run one explicit brain -> central scheduling loop."""
    state = create_turn_state(
        user_input=user_input,
        workspace=workspace,
        dialogue_history=dialogue_history or [],
        internal_history=internal_history or [],
        channel=channel,
        chat_id=chat_id,
        session_id=session_id,
    )
    state["metadata"] = dict(metadata or {})
    state["media"] = list(media or [])
    if on_progress is not None:
        state["on_progress"] = on_progress

    for _ in range(MAX_TURN_STEPS):
        state = await _run_brain_step(state, runtime)
        if state.get("done"):
            return str(state.get("output", "") or ""), state

        if _task_request_ready(state):
            state = await _run_central_step(state, runtime)
            if state.get("done"):
                return str(state.get("output", "") or ""), state
            continue

        logger.warning("Turn loop stopped without a finalized brain decision")
        break

    if not state.get("done"):
        brain = state.get("brain")
        task = state.get("task")
        fallback = ""
        if brain is not None:
            fallback = str(getattr(brain, "final_message", "") or "").strip()
        if not fallback and task is not None:
            fallback = str(getattr(task, "analysis", "") or "").strip()
        state["output"] = fallback or "我先给你一个当前能确认的结论，我们可以继续往下推进。"
        state["done"] = True

    return str(state.get("output", "") or ""), state


async def _run_brain_step(state: TurnState, runtime) -> TurnState:
    brain: BrainState = state["brain"]
    task: TaskState = state["task"]
    user_input = state["user_input"]
    dialogue_history = state.get("dialogue_history", [])
    metadata = state.get("metadata", {}) or {}
    loop_count = int(state.get("loop_count", 0) or 0)

    brain.task_action = ""
    brain.task_reason = ""

    paused_task = _extract_paused_task(metadata)
    if paused_task and not _has_task_packet(task):
        control = runtime.brain_decide_paused_task(
            user_input=user_input,
            task=paused_task,
            emotion=brain.emotion,
        )
        brain.task_action = str(control.get("action", "") or "")
        brain.task_reason = str(control.get("reason", "") or "")
        if brain.task_action == "resume_task":
            brain.task_request = str(
                paused_task.get("summary", "") or brain.task_brief or "继续当前执行"
            ).strip()
            delegation = _build_task_delegation(
                runtime,
                action="resume_task",
                user_input=user_input,
                task_brief=brain.task_request,
                intent=brain.intent,
                working_hypothesis=brain.working_hypothesis,
                session_id=state.get("session_id", ""),
                loop_count=loop_count + 1,
                task=dict(control.get("task", {}) or paused_task),
            )
            state["metadata"] = _set_task_metadata(
                metadata,
                dict(control.get("task", {}) or paused_task),
                delegation=delegation,
            )
            _queue_task_resume(task, user_input=user_input, task_context=paused_task)
            state["done"] = False
            return state

        state["metadata"] = _set_paused_task_metadata(
            metadata,
            dict(control.get("task", {}) or paused_task),
        )
        if brain.task_action == "defer":
            metadata = state["metadata"]
        else:
            brain.final_decision = str(control.get("final_decision", "answer") or "answer")
            brain.final_message = str(control.get("message", "") or "")
            state["output"] = brain.final_message
            state["done"] = True
            return state

    if not _has_task_packet(task):
        deliberation: BrainDeliberationPacket = await runtime.brain_deliberate(
            user_input=user_input,
            dialogue_history=dialogue_history,
            emotion=brain.emotion,
            pad=brain.pad,
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
            session_id=state.get("session_id", ""),
        )
        brain.intent = deliberation.get("intent", "")
        brain.working_hypothesis = deliberation.get("working_hypothesis", "")
        brain.retrieval_query = str(deliberation.get("retrieval_query", "") or user_input)
        brain.retrieval_focus = [
            str(item).strip()
            for item in list(deliberation.get("retrieval_focus", []) or [])
            if str(item).strip()
        ]
        brain.retrieved_memory_ids = [
            str(item).strip()
            for item in list(deliberation.get("retrieved_memory_ids", []) or [])
            if str(item).strip()
        ]
        brain.task_brief = deliberation.get("task_brief", "")
        brain.model_name = str(deliberation.get("model_name", "") or "")
        brain.prompt_tokens = int(deliberation.get("prompt_tokens", 0) or 0)
        brain.completion_tokens = int(deliberation.get("completion_tokens", 0) or 0)
        brain.total_tokens = int(deliberation.get("total_tokens", 0) or 0)

        control = runtime.brain_control_after_deliberation(
            deliberation=deliberation,
            emotion=brain.emotion,
        )
        brain.task_action = str(control.get("action", "") or "")
        brain.task_reason = str(control.get("reason", "") or "")

        if brain.task_action == "create_task":
            question = str(control.get("task_brief", "") or brain.task_brief or "")
            brain.task_request = question
            brain.task_brief = question
            delegation = _build_task_delegation(
                runtime,
                action="create_task",
                user_input=user_input,
                task_brief=question,
                intent=brain.intent,
                working_hypothesis=brain.working_hypothesis,
                session_id=state.get("session_id", ""),
                loop_count=loop_count + 1,
            )
            state["metadata"] = _merge_task_metadata(metadata, delegation=delegation)
            _queue_task_request(task, question)
            state["loop_count"] = loop_count + 1
            state["done"] = False
            return state

        brain.final_decision = str(control.get("final_decision", "answer") or "answer")
        brain.final_message = str(control.get("message", "") or "")
        state["output"] = brain.final_message
        state["done"] = True
        return state

    finalize: BrainFinalizePacket = await runtime.brain_finalize(
        user_input=user_input,
        history=dialogue_history,
        emotion=brain.emotion,
        pad=brain.pad,
        brain_intent=brain.intent,
        brain_working_hypothesis=brain.working_hypothesis,
        task_summary=runtime._build_task_summary({"task": task, "brain": brain}),
        task_status=task.status,
        task_missing=list(task.missing),
        task_recommended_action=task.recommended_action,
        loop_count=loop_count,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        session_id=state.get("session_id", ""),
    )

    brain.final_decision = str(finalize.get("decision", "") or "")
    brain.final_message = str(finalize.get("message", "") or "")
    brain.retrieval_query = str(finalize.get("retrieval_query", "") or brain.retrieval_query or user_input)
    brain.retrieval_focus = [
        str(item).strip()
        for item in list(finalize.get("retrieval_focus", []) or brain.retrieval_focus or [])
        if str(item).strip()
    ]
    brain.retrieved_memory_ids = [
        str(item).strip()
        for item in list(finalize.get("retrieved_memory_ids", []) or brain.retrieved_memory_ids or [])
        if str(item).strip()
    ]
    brain.task_brief = str(finalize.get("task_brief", "") or "")
    brain.model_name = str(finalize.get("model_name", "") or "")
    brain.prompt_tokens = int(finalize.get("prompt_tokens", 0) or 0)
    brain.completion_tokens = int(finalize.get("completion_tokens", 0) or 0)
    brain.total_tokens = int(finalize.get("total_tokens", 0) or 0)

    control = runtime.brain_control_after_finalize(
        finalize=finalize,
        loop_count=loop_count,
        max_loop_rounds=MAX_LOOP_ROUNDS,
        task_control_state=task.control_state,
        task_status=task.status,
        task_missing=list(task.missing),
        task_analysis=task.analysis,
        task_risks=list(task.risks),
    )
    brain.task_action = str(control.get("action", "") or "")
    brain.task_reason = str(control.get("reason", "") or "")
    brain.final_decision = str(control.get("final_decision", brain.final_decision) or brain.final_decision)
    brain.final_message = str(control.get("message", brain.final_message) or brain.final_message)
    brain.task_brief = str(
        control.get("task_brief", brain.task_brief) or brain.task_brief
    )

    if brain.task_action == "continue_task":
        question = brain.task_brief
        brain.task_request = question
        delegation = _build_task_delegation(
            runtime,
            action="continue_task",
            user_input=user_input,
            task_brief=question,
            intent=brain.intent,
            working_hypothesis=brain.working_hypothesis,
            session_id=state.get("session_id", ""),
            loop_count=loop_count + 1,
            task=_build_task_runtime_context(task),
        )
        state["metadata"] = _merge_task_metadata(
            metadata,
            delegation=delegation,
            task=_build_task_runtime_context(task),
        )
        _queue_task_request(task, question)
        state["loop_count"] = loop_count + 1
        state["done"] = False
        return state

    if brain.final_decision == "ask_user":
        state["output"] = brain.final_message or build_missing_info_prompt(task.missing)
    else:
        state["output"] = brain.final_message or task.analysis or "我先给你一个当前能确认的结论，我们可以继续往下推进。"
        brain.final_decision = "answer"

    state["done"] = True
    return state


async def _run_central_step(state: TurnState, runtime) -> TurnState:
    task: TaskState = state["task"]
    brain: BrainState = state["brain"]
    question = str(task.request or "").strip()
    metadata = state.get("metadata", {}) or {}
    task_context = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
    if not question and not task_context:
        state["done"] = True
        return state

    on_progress = state.get("on_progress")
    message_id = str(metadata.get("message_id", "") or "").strip()

    task.control_state = "running"
    task.status = "none"
    task_trace: list[dict[str, Any]] = []
    request_timestamp = datetime.now().isoformat()

    async def _on_trace(event: dict[str, Any]) -> None:
        if isinstance(event, dict):
            task_trace.append(dict(event))

    result = await runtime.run_central_task(
        request=question,
        history=state.get("internal_history", []),
        emotion=brain.emotion,
        pad=brain.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        session_id=state.get("session_id", ""),
        task_context=task_context,
        media=state.get("media"),
        on_progress=on_progress,
        on_trace=_on_trace,
    )

    task.attempts = task.attempts + 1
    task.thread_id = str(result.get("thread_id", "") or task.thread_id or "")
    task.run_id = str(result.get("run_id", "") or task.run_id or "")
    task.control_state = str(result.get("control_state", "completed") or "completed")
    task.status = str(result.get("status", "done") or "done")
    task.analysis = str(result.get("analysis", "") or "")
    task.result_summary = task.analysis
    task.risks = list(result.get("risks", []) or [])
    task.recommended_action = str(result.get("recommended_action", "") or "")
    task.confidence = float(result.get("confidence", 0.0) or 0.0)
    task.missing = list(result.get("missing", []) or [])
    task.pending_review = dict(result.get("pending_review", {}) or {})
    task.model_name = str(result.get("model_name", "") or "")
    task.prompt_tokens = int(result.get("prompt_tokens", 0) or 0)
    task.completion_tokens = int(result.get("completion_tokens", 0) or 0)
    task.total_tokens = int(result.get("total_tokens", 0) or 0)
    result_timestamp = datetime.now().isoformat()
    state["task_thread_id"] = task.thread_id
    state["task_run_id"] = task.run_id

    internal_history = list(state.get("internal_history", []) or [])
    internal_history.extend(
        [
            {
                "message_id": message_id,
                "role": "user",
                "content": json_text_block(question),
                "timestamp": request_timestamp,
            },
            {
                "message_id": message_id,
                "role": "assistant",
                "content": json_text_block(
                    {
                        "control_state": str(result.get("control_state", "") or ""),
                        "status": str(result.get("status", "") or ""),
                        "analysis": str(result.get("analysis", "") or ""),
                        "risks": list(result.get("risks", []) or []),
                        "missing": list(result.get("missing", []) or []),
                        "recommended_action": str(result.get("recommended_action", "") or ""),
                        "confidence": float(result.get("confidence", 0.0) or 0.0),
                    }
                ),
                **{
                    key: value
                    for key, value in {
                        "model_name": str(result.get("model_name", "") or ""),
                        "prompt_tokens": int(result.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(result.get("completion_tokens", 0) or 0),
                        "total_tokens": int(result.get("total_tokens", 0) or 0),
                    }.items()
                    if value not in ("", 0)
                },
                "timestamp": result_timestamp,
            },
        ]
    )
    state["internal_history"] = internal_history
    state["task_trace"] = task_trace
    return state


def _has_task_packet(task: TaskState) -> bool:
    if task.status in {"done", "need_more", "failed"}:
        return True
    return bool(task.analysis or task.attempts)


def _extract_paused_task(metadata: dict[str, Any]) -> dict[str, Any]:
    for key in ("paused_task", "task"):
        task = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
        if str((task or {}).get("control_state", "") or "").strip() == "paused":
            return dict(task)
    return {}


def _merge_task_metadata(
    metadata: dict[str, Any],
    *,
    delegation: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged.pop("paused_task", None)
    task_context = dict(task or {})
    if delegation:
        task_context["delegation"] = dict(delegation)
    merged["task"] = task_context
    return merged


def _set_task_metadata(
    metadata: dict[str, Any],
    task: dict[str, Any],
    *,
    delegation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated.pop("paused_task", None)
    task_payload = dict(task or {})
    if delegation:
        task_payload["delegation"] = dict(delegation)
    updated["task"] = task_payload
    return updated


def _set_paused_task_metadata(metadata: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated.pop("task", None)
    if task:
        updated["paused_task"] = dict(task)
    else:
        updated.pop("paused_task", None)
    return updated


def _queue_task_request(task: TaskState, question: str) -> None:
    task.request = question
    task.goal = question
    task.control_state = "running"
    task.status = "none"
    task.analysis = ""
    task.result_summary = ""
    task.risks = []
    task.recommended_action = ""
    task.confidence = 0.0
    task.missing = []
    task.pending_review = {}


def _queue_task_resume(task: TaskState, *, user_input: str, task_context: dict[str, Any]) -> None:
    request = str(user_input or "").strip()
    if _is_plain_resume_signal(request):
        request = ""
    task.request = str(request or task_context.get("summary", "") or "继续上次执行").strip()
    task.goal = task.request
    task.thread_id = str(task_context.get("thread_id", "") or "")
    task.run_id = str(task_context.get("run_id", "") or "")
    task.control_state = "running"
    task.status = "none"
    task.analysis = ""
    task.result_summary = ""
    task.risks = []
    task.recommended_action = ""
    task.confidence = 0.0
    task.missing = [
        str(item).strip()
        for item in (task_context.get("missing", []) or [])
        if str(item).strip()
    ]
    task.pending_review = dict(task_context.get("pending_review", {}) or {})


def _build_task_runtime_context(task: TaskState) -> dict[str, Any]:
    return {
        "invoked": True,
        "thread_id": str(task.thread_id or "").strip(),
        "run_id": str(task.run_id or "").strip(),
        "control_state": str(task.control_state or "idle").strip(),
        "status": str(task.status or "none").strip(),
        "summary": str(task.analysis or "").strip(),
        "risks": list(task.risks or []),
        "recommended_action": str(task.recommended_action or "").strip(),
        "confidence": float(task.confidence or 0.0),
        "missing": [str(item).strip() for item in list(task.missing or []) if str(item).strip()],
        "pending_review": dict(task.pending_review or {}),
    }


def _build_task_delegation(runtime, **kwargs) -> dict[str, Any]:
    builder = getattr(runtime, "brain_build_task_delegation", None)
    if callable(builder):
        return dict(builder(**kwargs) or {})
    question = str(kwargs.get("task_brief", "") or "").strip()
    return {
        "goal": question,
        "request": question,
        "constraints": [],
        "relevant_task_memories": [],
        "relevant_tool_memories": [],
        "skill_hints": [],
        "success_criteria": [],
        "return_contract": {"mode": "final_only", "must_not": []},
    }


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


__all__ = ["run_turn_engine"]

