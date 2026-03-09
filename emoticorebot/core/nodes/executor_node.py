"""Executor node for orchestration."""

from __future__ import annotations

from datetime import datetime

from emoticorebot.core.state import ExecutorResultPacket, ExecutorState, MainBrainState, OrchestrationState
from emoticorebot.utils.llm_utils import json_text_block


async def executor_node(state: OrchestrationState, runtime) -> OrchestrationState:
    executor: ExecutorState = state["executor"]
    main_brain: MainBrainState = state["main_brain"]
    question = str(executor.request or "").strip()
    if not question:
        state["done"] = True
        return state

    metadata = state.get("metadata", {}) or {}
    on_progress = state.get("on_progress")
    intent_params = metadata.get("intent_params") if isinstance(metadata.get("intent_params"), dict) else None
    message_id = str(metadata.get("message_id", "") or "").strip()

    executor.status = "running"
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
        intent_params=intent_params,
        media=state.get("media"),
        on_progress=on_progress,
        on_trace=_on_trace,
    )

    executor.attempts = executor.attempts + 1
    executor.status = str(result.get("status", "uncertain") or "uncertain")
    executor.analysis = str(result.get("analysis", "") or "")
    executor.risks = list(result.get("risks", []) or [])
    executor.recommended_action = str(result.get("recommended_action", "") or "")
    executor.confidence = float(result.get("confidence", 0.0) or 0.0)
    executor.missing_params = list(result.get("missing", []) or [])
    executor.model_name = str(result.get("model_name", "") or "")
    executor.prompt_tokens = int(result.get("prompt_tokens", 0) or 0)
    executor.completion_tokens = int(result.get("completion_tokens", 0) or 0)
    executor.total_tokens = int(result.get("total_tokens", 0) or 0)
    result_timestamp = datetime.now().isoformat()

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
