"""IQ Node - 执行 EQ 发起的内部理性分析。"""

from __future__ import annotations

from emoticorebot.core.state import EQState, FusionState, IQState


async def iq_node(state: FusionState, runtime) -> FusionState:
    iq: IQState = state["iq"]
    eq: EQState = state["eq"]
    task = str(iq.task or "").strip()
    if not task:
        state["done"] = True
        return state

    metadata = state.get("metadata", {}) or {}
    on_progress = state.get("on_progress")
    intent_params = metadata.get("intent_params") if isinstance(metadata.get("intent_params"), dict) else None

    iq.status = "running"
    result = await runtime.run_iq_task(
        task=task,
        history=state.get("history", []),
        emotion=eq.emotion,
        pad=eq.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        intent_params=intent_params,
        media=state.get("media"),
        on_progress=on_progress,
    )

    iq.attempts = iq.attempts + 1
    iq.status = str(result.get("status", "uncertain") or "uncertain")
    iq.analysis = str(result.get("analysis", "") or "")
    iq.evidence = list(result.get("evidence", []) or [])
    iq.risks = list(result.get("risks", []) or [])
    iq.options = list(result.get("options", []) or [])
    iq.recommended_action = str(result.get("recommended_action", "") or "")
    iq.selected_experts = list(result.get("selected_experts", []) or [])
    iq.expert_packets = list(result.get("expert_packets", []) or [])
    iq.tool_calls = list(result.get("tool_calls", []) or [])
    iq.iterations = int(result.get("iterations", 0) or 0)
    iq.confidence = float(result.get("confidence", 0.0) or 0.0)
    iq.rationale_summary = str(result.get("rationale_summary", "") or "")
    iq.missing_params = list(result.get("missing", []) or [])

    if iq.status == "completed":
        iq.error = ""
    else:
        iq.error = iq.analysis

    return state
