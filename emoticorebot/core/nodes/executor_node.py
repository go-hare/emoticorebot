"""Executor node for the explicit turn loop."""

from __future__ import annotations

from datetime import datetime

from emoticorebot.core.state import ExecutorResultPacket, ExecutorState, MainBrainState, TurnState
from emoticorebot.utils.llm_utils import json_text_block


async def executor_node(state: TurnState, runtime) -> TurnState:
    executor: ExecutorState = state["executor"]
    main_brain: MainBrainState = state["main_brain"]
    question = str(executor.request or "").strip()
    metadata = state.get("metadata", {}) or {}
    execution_context = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else None
    if not question and not execution_context:
        state["done"] = True
        return state

    on_progress = state.get("on_progress")
    message_id = str(metadata.get("message_id", "") or "").strip()

    executor.control_state = "running"
    executor.status = "none"
    executor_trace: list[dict] = []
    request_timestamp = datetime.now().isoformat()

    async def _on_trace(event: dict) -> None:
        if isinstance(event, dict):
            executor_trace.append(dict(event))

    result: ExecutorResultPacket = await runtime.run_executor_request(
        request=question,
        history=state.get("internal_history", []),
        emotion=main_brain.emotion,
        pad=main_brain.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        session_id=state.get("session_id", ""),
        execution_context=execution_context,
        media=state.get("media"),
        on_progress=on_progress,
        on_trace=_on_trace,
    )

    executor.attempts = executor.attempts + 1
    executor.thread_id = str(result.get("thread_id", "") or executor.thread_id or "")
    executor.run_id = str(result.get("run_id", "") or executor.run_id or "")
    executor.control_state = str(result.get("control_state", "completed") or "completed")
    executor.status = str(result.get("status", "done") or "done")
    executor.analysis = str(result.get("analysis", "") or "")
    executor.final_result = executor.analysis
    executor.risks = list(result.get("risks", []) or [])
    executor.recommended_action = str(result.get("recommended_action", "") or "")
    executor.confidence = float(result.get("confidence", 0.0) or 0.0)
    executor.missing = list(result.get("missing", []) or [])
    executor.pending_review = dict(result.get("pending_review", {}) or {})
    executor.model_name = str(result.get("model_name", "") or "")
    executor.prompt_tokens = int(result.get("prompt_tokens", 0) or 0)
    executor.completion_tokens = int(result.get("completion_tokens", 0) or 0)
    executor.total_tokens = int(result.get("total_tokens", 0) or 0)
    result_timestamp = datetime.now().isoformat()
    state["executor_thread_id"] = executor.thread_id
    state["executor_run_id"] = executor.run_id

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
    state["executor_trace"] = executor_trace

    return state
