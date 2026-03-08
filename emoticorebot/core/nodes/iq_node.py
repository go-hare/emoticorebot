"""IQ Node - 执行 EQ 发起的内部理性分析。"""

from __future__ import annotations

from datetime import datetime

from emoticorebot.core.state import EQState, FusionState, IQResultPacket, IQState
from emoticorebot.utils.llm_utils import json_text_block


async def iq_node(state: FusionState, runtime) -> FusionState:
    iq: IQState = state["iq"]
    eq: EQState = state["eq"]
    question = str(iq.request or "").strip()
    if not question:
        state["done"] = True
        return state

    metadata = state.get("metadata", {}) or {}
    on_progress = state.get("on_progress")
    intent_params = metadata.get("intent_params") if isinstance(metadata.get("intent_params"), dict) else None
    message_id = str(metadata.get("message_id", "") or "").strip()

    iq.status = "running"
    iq_trace: list[dict] = []
    request_timestamp = datetime.now().isoformat()

    async def _on_trace(event: dict) -> None:
        if isinstance(event, dict):
            iq_trace.append(dict(event))

    result: IQResultPacket = await runtime.run_iq_request(
        request=question,
        history=state.get("eq_iq_history", []),
        emotion=eq.emotion,
        pad=eq.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        session_id=state.get("session_id", ""),
        intent_params=intent_params,
        media=state.get("media"),
        on_progress=on_progress,
        on_trace=_on_trace,
    )

    iq.attempts = iq.attempts + 1
    iq.status = str(result.get("status", "uncertain") or "uncertain")
    iq.analysis = str(result.get("analysis", "") or "")
    iq.risks = list(result.get("risks", []) or [])
    iq.recommended_action = str(result.get("recommended_action", "") or "")
    iq.confidence = float(result.get("confidence", 0.0) or 0.0)
    iq.missing_params = list(result.get("missing", []) or [])
    iq.model_name = str(result.get("model_name", "") or "")
    iq.prompt_tokens = int(result.get("prompt_tokens", 0) or 0)
    iq.completion_tokens = int(result.get("completion_tokens", 0) or 0)
    iq.total_tokens = int(result.get("total_tokens", 0) or 0)
    result_timestamp = datetime.now().isoformat()

    # `eq_iq_history` is single-turn internal deliberation only.
    # We append the current EQ->IQ question and IQ->EQ result so later internal
    # rounds in the same turn can see prior failures / summaries.
    eq_iq_history = list(state.get("eq_iq_history", []) or [])
    eq_iq_history.extend(
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
    state["eq_iq_history"] = eq_iq_history
    state["iq_trace"] = iq_trace

    return state
