"""Shared helpers for persisted IQ context summaries."""

from __future__ import annotations

from typing import Any


def compact_text(text: str, limit: int = 400) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def build_expert_disagreement_summary(expert_packets: list[dict[str, Any]]) -> str:
    if len(expert_packets) < 2:
        return ""
    actions = {
        str(packet.get("proposed_action", "") or "").strip()
        for packet in expert_packets
        if str(packet.get("proposed_action", "") or "").strip()
    }
    missing_sets = {
        tuple(str(item).strip() for item in packet.get("missing", []) if str(item).strip())
        for packet in expert_packets
        if isinstance(packet, dict) and packet.get("missing")
    }
    risk_count = sum(
        1
        for packet in expert_packets
        if isinstance(packet, dict) and any(str(item).strip() for item in packet.get("risks", []))
    )

    parts: list[str] = []
    if len(actions) > 1:
        action_map = ", ".join(
            f"{str(packet.get('expert', 'unknown')).strip()}->{str(packet.get('proposed_action', '')).strip()}"
            for packet in expert_packets
            if isinstance(packet, dict) and str(packet.get("proposed_action", "") or "").strip()
        )
        if action_map:
            parts.append(f"动作分歧: {action_map}")
    if len(missing_sets) > 1:
        parts.append("缺参判断不一致")
    if risk_count and risk_count < len(expert_packets):
        parts.append("风险判断不一致")
    return "；".join(parts[:3])


def build_expert_packet_summaries(expert_packets: list[dict[str, Any]]) -> list[str]:
    summaries: list[str] = []
    for packet in expert_packets[:3]:
        if not isinstance(packet, dict):
            continue
        expert = str(packet.get("expert", "") or "unknown").strip()
        status = str(packet.get("status", "") or "unknown").strip()
        confidence = float(packet.get("confidence", 0.0) or 0.0)
        answer = compact_text(str(packet.get("answer", "") or "").strip(), limit=80)
        action = str(packet.get("proposed_action", "") or "").strip()
        parts = [f"{expert}[{status}|{confidence:.2f}]"]
        if action:
            parts.append(f"动作={action}")
        if answer:
            parts.append(answer)
        summaries.append("；".join(parts))
    return summaries


def extract_memory_overlay_metadata(expert_packets: list[dict[str, Any]]) -> dict[str, str]:
    for packet in expert_packets:
        if not isinstance(packet, dict):
            continue
        if str(packet.get("expert", "") or "").strip() != "MemoryOverlay":
            continue
        metadata = packet.get("metadata", {}) if isinstance(packet.get("metadata"), dict) else {}
        return {
            "kind": str(metadata.get("kind", "") or "").strip(),
            "resume_task": str(metadata.get("resume_task", "") or "").strip(),
            "summary": str(metadata.get("summary", "") or packet.get("answer", "") or "").strip(),
        }
    return {"kind": "", "resume_task": "", "summary": ""}


def build_iq_context(message: dict[str, Any], *, summary_limit: int = 500) -> str:
    summary = compact_text(str(message.get("iq_summary", "")).strip(), limit=summary_limit)
    if summary:
        return summary

    task = compact_text(str(message.get("iq_task", "")).strip(), limit=96)
    status = compact_text(str(message.get("iq_status", "")).strip(), limit=24)
    analysis = compact_text(str(message.get("iq_analysis", "")).strip(), limit=160)
    confidence = float(message.get("iq_confidence", 0.0) or 0.0)
    rationale = compact_text(str(message.get("iq_rationale_summary", "")).strip(), limit=90)
    error = compact_text(str(message.get("iq_error", "")).strip(), limit=120)
    recommended_action = compact_text(str(message.get("iq_recommended_action", "")).strip(), limit=36)
    selected_experts = [str(item).strip() for item in message.get("iq_selected_experts", []) if str(item).strip()]
    accepted_experts = [str(item).strip() for item in message.get("eq_accepted_experts", []) if str(item).strip()]
    rejected_experts = [str(item).strip() for item in message.get("eq_rejected_experts", []) if str(item).strip()]
    arbitration_summary = compact_text(str(message.get("eq_arbitration_summary", "")).strip(), limit=120)
    expert_packets = [packet for packet in message.get("iq_expert_packets", []) if isinstance(packet, dict)]
    disagreement = compact_text(
        str(message.get("iq_disagreement_summary", "") or build_expert_disagreement_summary(expert_packets)).strip(),
        limit=120,
    )
    memory_overlay = extract_memory_overlay_metadata(expert_packets)
    memory_kind = compact_text(
        str(message.get("iq_memory_overlay_kind", "") or memory_overlay.get("kind", "")).strip(),
        limit=40,
    )
    memory_resume_task = compact_text(
        str(message.get("iq_memory_resume_task", "") or memory_overlay.get("resume_task", "")).strip(),
        limit=80,
    )
    memory_summary = compact_text(
        str(message.get("iq_memory_overlay_summary", "") or memory_overlay.get("summary", "")).strip(),
        limit=100,
    )
    missing = [str(item).strip() for item in message.get("iq_missing_params", []) if str(item).strip()]
    tool_calls = [
        str(call.get("tool", "")).strip()
        for call in message.get("iq_tool_calls", [])
        if isinstance(call, dict) and str(call.get("tool", "")).strip()
    ]

    parts: list[str] = []
    label = f"[IQ|{status or 'unknown'}|{confidence:.2f}]" if confidence > 0 else f"[IQ|{status or 'unknown'}]"
    parts.append(label)
    if task:
        parts.append(f"任务: {task}")
    if selected_experts:
        parts.append(f"专家: {', '.join(selected_experts[:3])}")
    if analysis:
        parts.append(f"分析: {analysis}")
    elif error:
        parts.append(f"异常/追问线索: {error}")
    if recommended_action:
        parts.append(f"建议动作: {recommended_action}")
    if memory_kind:
        parts.append(f"记忆命中: {memory_kind}")
    if memory_resume_task:
        parts.append(f"恢复任务: {memory_resume_task}")
    elif memory_summary:
        parts.append(f"记忆补丁: {memory_summary}")
    if rationale:
        parts.append(f"依据: {rationale}")
    if arbitration_summary:
        parts.append(f"EQ裁决: {arbitration_summary}")
    elif accepted_experts or rejected_experts:
        accepted = "、".join(accepted_experts[:3]) if accepted_experts else "无"
        rejected = "、".join(rejected_experts[:3]) if rejected_experts else "无"
        parts.append(f"EQ裁决: 采纳 {accepted}；压过 {rejected}")
    if disagreement:
        parts.append(f"分歧: {disagreement}")
    expert_summaries = build_expert_packet_summaries(expert_packets)
    if expert_summaries:
        parts.append(f"专家摘要: {' | '.join(expert_summaries)}")
    if missing:
        parts.append(f"缺失参数: {', '.join(missing[:5])}")
    if tool_calls:
        unique_tools = list(dict.fromkeys(tool_calls))
        parts.append(f"工具: {', '.join(unique_tools[:3])}")

    return "；".join(parts)


__all__ = [
    "build_expert_disagreement_summary",
    "build_expert_packet_summaries",
    "build_iq_context",
    "compact_text",
    "extract_memory_overlay_metadata",
]
