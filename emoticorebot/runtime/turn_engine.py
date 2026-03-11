"""Explicit turn scheduler for the brain -> task-system loop."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.agent.brain_user_turn import build_default_task_brief
from emoticorebot.agent.reply_utils import build_missing_info_prompt
from emoticorebot.agent.state import (
    BrainControlPacket,
    BrainState,
    TurnState,
    create_turn_state,
)
from emoticorebot.runtime.event_bus import TaskSignal
from emoticorebot.tasks import TaskState
from emoticorebot.utils.llm_utils import json_text_block

MAX_TURN_STEPS = 8
MAX_LOOP_ROUNDS = 3


def _task_command_ready(state: TurnState) -> bool:
    metadata = state.get("metadata", {}) or {}
    pending = metadata.get("task_command") if isinstance(metadata.get("task_command"), dict) else {}
    return bool(str(pending.get("action", "") or "").strip())


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
    """Run one explicit brain -> task/bus scheduling loop."""
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

        if _task_command_ready(state):
            state = await _run_task_step(state, runtime)
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

    if not _has_task_packet(task):
        paused_task = _extract_paused_task(metadata)
        control: BrainControlPacket = await runtime.brain_handle_user_turn(
            user_input=user_input,
            dialogue_history=dialogue_history,
            emotion=brain.emotion,
            pad=brain.pad,
            paused_task=paused_task or None,
            message_id=str(metadata.get("message_id", "") or ""),
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
            session_id=state.get("session_id", ""),
        )
        if paused_task and str(control.get("action", "") or "").strip() == "defer":
            state["metadata"] = _set_paused_task_metadata(
                metadata,
                dict(control.get("task", {}) or paused_task),
            )
            metadata = state["metadata"]
            control = await runtime.brain_handle_user_turn(
                user_input=user_input,
                dialogue_history=dialogue_history,
                emotion=brain.emotion,
                pad=brain.pad,
                paused_task=None,
                message_id=str(metadata.get("message_id", "") or ""),
                channel=state.get("channel", ""),
                chat_id=state.get("chat_id", ""),
                session_id=state.get("session_id", ""),
            )

        _apply_brain_control(brain, control, default_query=user_input)
        if brain.task_action in {"create_task", "resume_task"}:
            question = str(control.get("task_brief", "") or brain.task_brief or "")
            if brain.task_action == "resume_task":
                task_context = dict(control.get("task", {}) or paused_task or {})
                if not question:
                    question = str(task_context.get("summary", "") or "继续当前执行").strip()
                state["metadata"] = _set_pending_task_command(
                    _set_task_metadata(
                        metadata,
                        task_context,
                        delegation=_build_task_delegation(
                            runtime,
                            action="resume_task",
                            user_input=user_input,
                            task_brief=question,
                            intent=brain.intent,
                            working_hypothesis=brain.working_hypothesis,
                            session_id=state.get("session_id", ""),
                            loop_count=loop_count + 1,
                            task=task_context,
                        ),
                    ),
                    _build_pending_task_command(
                        action="resume_task",
                        user_input=user_input,
                        task_brief=question,
                    ),
                )
            else:
                state["metadata"] = _set_pending_task_command(
                    _merge_task_metadata(
                        metadata,
                        delegation=_build_task_delegation(
                            runtime,
                            action="create_task",
                            user_input=user_input,
                            task_brief=question,
                            intent=brain.intent,
                            working_hypothesis=brain.working_hypothesis,
                            session_id=state.get("session_id", ""),
                            loop_count=loop_count + 1,
                        ),
                    ),
                    _build_pending_task_command(
                        action="create_task",
                        user_input=user_input,
                        task_brief=question,
                    ),
                )
            brain.task_request = question
            brain.task_brief = question
            state["loop_count"] = loop_count + 1
            state["done"] = False
            return state

        if paused_task:
            state["metadata"] = _set_paused_task_metadata(
                metadata,
                dict(control.get("task", {}) or paused_task),
            )
        state["output"] = brain.final_message
        state["done"] = True
        return state

    control = await runtime.brain_handle_task_signal(
        signal=_build_task_result_signal(
            task,
            session_id=str(state.get("session_id", "") or ""),
            message_id=str(metadata.get("message_id", "") or ""),
        ),
        user_input=user_input,
        dialogue_history=dialogue_history,
        emotion=brain.emotion,
        pad=brain.pad,
        brain_intent=brain.intent,
        brain_working_hypothesis=brain.working_hypothesis,
        loop_count=loop_count,
        max_loop_rounds=MAX_LOOP_ROUNDS,
        task=_build_task_runtime_context(task),
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        session_id=state.get("session_id", ""),
    )
    _apply_brain_control(brain, control, default_query=user_input)

    if brain.task_action == "continue_task":
        question = brain.task_brief
        brain.task_request = question
        state["metadata"] = _set_pending_task_command(
            _merge_task_metadata(
                metadata,
                delegation=_build_task_delegation(
                    runtime,
                    action="continue_task",
                    user_input=user_input,
                    task_brief=question,
                    intent=brain.intent,
                    working_hypothesis=brain.working_hypothesis,
                    session_id=state.get("session_id", ""),
                    loop_count=loop_count + 1,
                    task=_build_task_runtime_context(task),
                ),
                task=_build_task_runtime_context(task),
            ),
            _build_pending_task_command(
                action="continue_task",
                user_input=user_input,
                task_brief=question,
            ),
        )
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


async def _run_task_step(state: TurnState, runtime) -> TurnState:
    task: TaskState = state["task"]
    metadata = state.get("metadata", {}) or {}
    pending = metadata.get("task_command") if isinstance(metadata.get("task_command"), dict) else {}
    task_context = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
    action = str(pending.get("action", "") or "").strip()
    task_brief = str(pending.get("task_brief", "") or "").strip()
    if not action:
        state["done"] = True
        return state

    question = _prepare_task_request(
        task,
        action=action,
        task_brief=task_brief,
        task_context=task_context,
        user_input=str(state.get("user_input", "") or ""),
    )
    if not question and not task_context:
        state["done"] = True
        return state

    task_trace: list[dict[str, Any]] = []
    signal_queue = runtime.bus.subscribe_task_signals()
    signal_stop = False
    signal_task = None
    on_progress = state.get("on_progress")
    message_id = str(metadata.get("message_id", "") or "").strip()
    session_id = str(state.get("session_id", "") or "").strip()

    async def _on_trace(event: dict[str, Any]) -> None:
        await runtime.publish_task_signal(
            _build_task_signal(
                event,
                session_id=session_id,
                message_id=message_id,
                task_id=str(task.task_id or ""),
            )
        )

    async def _relay_signals() -> None:
        nonlocal signal_stop
        while not signal_stop:
            try:
                signal = await asyncio.wait_for(signal_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if signal.session_id != session_id:
                continue
            if message_id and signal.message_id and signal.message_id != message_id:
                continue
            record = _task_trace_from_signal(signal)
            if record:
                task_trace.append(record)
            producer = str((signal.payload or {}).get("producer", "") or "").strip().lower()
            if producer != "brain" and str(signal.event or "").strip().lower() != "task.result":
                await runtime.brain_handle_task_signal(
                    signal=signal,
                    user_input=str(state.get("user_input", "") or ""),
                    dialogue_history=state.get("dialogue_history", []),
                    emotion=state["brain"].emotion,
                    pad=state["brain"].pad,
                    brain_intent=state["brain"].intent,
                    brain_working_hypothesis=state["brain"].working_hypothesis,
                    loop_count=int(state.get("loop_count", 0) or 0),
                    max_loop_rounds=MAX_LOOP_ROUNDS,
                    task=_build_task_runtime_context(task),
                    channel=state.get("channel", ""),
                    chat_id=state.get("chat_id", ""),
                    session_id=session_id,
                )
            if on_progress is not None and signal.content:
                await on_progress(signal.content)

    signal_task = asyncio.create_task(_relay_signals(), name=f"task-signals:{session_id or 'default'}")
    try:
        result = await runtime.run_central_task(
            request=question,
            history=state.get("internal_history", []),
            emotion=state["brain"].emotion,
            pad=state["brain"].pad,
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
            session_id=session_id,
            task_context=task_context,
            media=state.get("media"),
            on_progress=on_progress,
            on_trace=_on_trace,
        )
        _apply_central_result(task, result)
        await runtime.publish_task_signal(
            _build_task_result_signal(
                task,
                session_id=session_id,
                message_id=message_id,
            )
        )
    finally:
        signal_stop = True
        if signal_task is not None:
            try:
                await signal_task
            finally:
                runtime.bus.unsubscribe_task_signals(signal_queue)

    state["task_thread_id"] = task.thread_id
    state["task_run_id"] = task.run_id
    state["internal_history"] = _append_internal_history(
        state.get("internal_history", []),
        question=question,
        result=result,
        message_id=message_id,
    )
    state["task_trace"] = task_trace
    if task.control_state == "paused":
        state["metadata"] = _set_pending_task_command(
            _set_paused_task_metadata(metadata, _build_task_runtime_context(task)),
            None,
        )
    else:
        state["metadata"] = _set_pending_task_command(
            _set_task_metadata(metadata, _build_task_runtime_context(task)),
            None,
        )
    return state


def _apply_brain_control(brain: BrainState, control: BrainControlPacket, *, default_query: str) -> None:
    brain.intent = str(control.get("intent", "") or brain.intent)
    brain.working_hypothesis = str(control.get("working_hypothesis", "") or brain.working_hypothesis)
    brain.task_action = str(control.get("action", "") or "")
    brain.task_reason = str(control.get("reason", "") or "")
    brain.final_decision = str(control.get("final_decision", "") or brain.final_decision)
    brain.final_message = str(control.get("message", "") or brain.final_message)
    brain.task_brief = str(control.get("task_brief", "") or brain.task_brief)
    brain.retrieval_query = str(control.get("retrieval_query", "") or brain.retrieval_query or default_query)
    brain.retrieval_focus = [
        str(item).strip()
        for item in list(control.get("retrieval_focus", []) or brain.retrieval_focus or [])
        if str(item).strip()
    ]
    brain.retrieved_memory_ids = [
        str(item).strip()
        for item in list(control.get("retrieved_memory_ids", []) or brain.retrieved_memory_ids or [])
        if str(item).strip()
    ]
    brain.model_name = str(control.get("model_name", "") or brain.model_name)
    brain.prompt_tokens = int(control.get("prompt_tokens", brain.prompt_tokens) or brain.prompt_tokens)
    brain.completion_tokens = int(control.get("completion_tokens", brain.completion_tokens) or brain.completion_tokens)
    brain.total_tokens = int(control.get("total_tokens", brain.total_tokens) or brain.total_tokens)


def _build_task_result_signal(
    task: TaskState,
    *,
    session_id: str,
    message_id: str,
) -> TaskSignal:
    payload = {
        "producer": "task_system",
        "control_state": str(task.control_state or "").strip(),
        "status": str(task.status or "").strip(),
        "analysis": str(task.analysis or "").strip(),
        "summary": str(task.analysis or "").strip(),
        "risks": list(task.risks or []),
        "missing": [str(item).strip() for item in list(task.missing or []) if str(item).strip()],
        "recommended_action": str(task.recommended_action or "").strip(),
        "confidence": float(task.confidence or 0.0),
        "pending_review": dict(task.pending_review or {}),
    }
    return TaskSignal(
        session_id=session_id,
        message_id=message_id,
        task_id=str(task.task_id or "").strip(),
        event="task.result",
        content=str(task.analysis or "").strip(),
        payload=payload,
    )


def _prepare_task_request(
    task: TaskState,
    *,
    action: str,
    task_brief: str,
    task_context: dict[str, Any],
    user_input: str,
) -> str:
    now = datetime.now().isoformat()
    if str(action or "").strip() == "resume_task":
        request = str(user_input or "").strip()
        if _is_plain_resume_signal(request):
            request = ""
        task.request = str(request or task_context.get("summary", "") or "继续上次执行").strip()
        task.goal = task.request
        task.task_id = str(task_context.get("task_id", "") or task.task_id or "")
        task.title = str(task_context.get("title", "") or task.title or "")
        task.thread_id = str(task_context.get("thread_id", "") or "")
        task.run_id = str(task_context.get("run_id", "") or "")
        task.plan = list(task_context.get("plan", []) or [])
        task.artifacts = list(task_context.get("artifacts", []) or [])
        task.created_at = str(task_context.get("created_at", "") or task.created_at or now)
        task.missing = [
            str(item).strip()
            for item in (task_context.get("missing", []) or [])
            if str(item).strip()
        ]
        task.pending_review = dict(task_context.get("pending_review", {}) or {})
    else:
        task.request = str(task_brief or "").strip()
        task.goal = task.request
    task.control_state = "running"
    task.status = "none"
    task.created_at = task.created_at or now
    task.updated_at = now
    task.analysis = ""
    task.result_summary = ""
    task.risks = []
    task.recommended_action = ""
    task.confidence = 0.0
    return str(task.request or "").strip()


def _apply_central_result(task: TaskState, result: dict[str, Any]) -> None:
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


def _append_internal_history(
    internal_history: list[dict[str, Any]] | None,
    *,
    question: str,
    result: dict[str, Any],
    message_id: str,
) -> list[dict[str, Any]]:
    request_timestamp = datetime.now().isoformat()
    result_timestamp = datetime.now().isoformat()
    history = list(internal_history or [])
    history.extend(
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
    return history


def _build_task_signal(
    trace: dict[str, Any],
    *,
    session_id: str,
    message_id: str,
    task_id: str,
) -> TaskSignal:
    return TaskSignal(
        session_id=session_id,
        message_id=message_id,
        task_id=task_id,
        event=str(trace.get("event", "") or trace.get("phase", "") or "task.progress"),
        content=str(trace.get("content", "") or "").strip(),
        payload=dict(trace),
    )


def _task_trace_from_signal(signal: TaskSignal) -> dict[str, Any]:
    payload = dict(signal.payload or {})
    if payload:
        payload.setdefault("timestamp", signal.timestamp.isoformat())
        return payload
    return {
        "role": "assistant",
        "phase": "task_trace",
        "event": signal.event,
        "content": signal.content,
        "timestamp": signal.timestamp.isoformat(),
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


def _set_pending_task_command(metadata: dict[str, Any], pending: dict[str, Any] | None) -> dict[str, Any]:
    updated = dict(metadata or {})
    if pending:
        updated["task_command"] = dict(pending)
    else:
        updated.pop("task_command", None)
    return updated


def _build_pending_task_command(*, action: str, user_input: str, task_brief: str) -> dict[str, Any]:
    return {
        "action": str(action or "").strip(),
        "user_input": str(user_input or "").strip(),
        "task_brief": str(task_brief or "").strip(),
    }


def _build_task_runtime_context(task: TaskState) -> dict[str, Any]:
    return {
        "invoked": True,
        "task_id": str(task.task_id or "").strip(),
        "title": str(task.title or "").strip(),
        "goal": str(task.goal or "").strip(),
        "plan": list(task.plan or []),
        "artifacts": list(task.artifacts or []),
        "created_at": str(task.created_at or "").strip(),
        "updated_at": str(task.updated_at or "").strip(),
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


__all__ = ["run_turn_engine"]

