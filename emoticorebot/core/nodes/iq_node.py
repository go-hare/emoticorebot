"""IQ Node - 执行 EQ 发起的内部理性分析。"""

from __future__ import annotations

from emoticorebot.core.state import EQState, FusionState, IQState
from emoticorebot.session.iq_context import build_iq_context


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
    task_id = str(metadata.get("task_id", "") or "").strip()

    iq.status = "running"
    result = await runtime.run_iq_task(
        task=task,
        history=state.get("eq_iq_history", []),
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

    # `eq_iq_history` is single-turn internal deliberation only.
    # We append the current EQ->IQ task and IQ->EQ result so later internal
    # rounds in the same turn can see prior failures / summaries.
    eq_iq_history = list(state.get("eq_iq_history", []) or [])
    eq_iq_history.extend(
        [
            {
                "role": "user",
                "phase": "eq_to_iq",
                "task_id": task_id,
                "task": task,
                "content": task,
                "selected_experts": list(eq.selected_experts or []),
                "expert_questions": dict(eq.expert_questions or {}),
            },
            {
                "role": "assistant",
                "phase": "iq_to_eq",
                "task_id": task_id,
                "task": task,
                "content": _build_internal_iq_result_summary(task, result),
                "iq_status": str(result.get("status", "") or ""),
                "iq_confidence": float(result.get("confidence", 0.0) or 0.0),
                "iq_selected_experts": list(result.get("selected_experts", []) or []),
                "iq_missing_params": list(result.get("missing", []) or []),
                "iq_tool_calls": list(result.get("tool_calls", []) or []),
                "iq_expert_packets": list(result.get("expert_packets", []) or []),
            },
        ]
    )
    state["eq_iq_history"] = eq_iq_history

    return state


def _build_internal_iq_result_summary(task: str, result: dict) -> str:
    summary = build_iq_context(
        {
            "iq_task": task,
            "iq_status": str(result.get("status", "") or ""),
            "iq_analysis": str(result.get("analysis", "") or ""),
            "iq_recommended_action": str(result.get("recommended_action", "") or ""),
            "iq_selected_experts": list(result.get("selected_experts", []) or []),
            "iq_expert_packets": list(result.get("expert_packets", []) or []),
            "iq_confidence": float(result.get("confidence", 0.0) or 0.0),
            "iq_rationale_summary": str(result.get("rationale_summary", "") or ""),
            "iq_error": str(result.get("analysis", "") or ""),
            "iq_missing_params": list(result.get("missing", []) or []),
            "iq_tool_calls": list(result.get("tool_calls", []) or []),
        },
        summary_limit=320,
    )
    return summary or str(result.get("analysis", "") or "").strip() or "[IQ] 未返回有效结果"
