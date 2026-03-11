"""Central result normalization helpers."""

from __future__ import annotations

import json
from typing import Any

from emoticorebot.tasks import CentralResultPacket
from emoticorebot.tasks.state_machine import (
    CENTRAL_PACKET_STATUSES,
    TASK_RECOMMENDED_ACTIONS,
)
from emoticorebot.utils.llm_utils import extract_message_metrics


def normalize_result_packet(
    raw_result: Any,
    *,
    request: str,
    task_context: dict[str, Any] | None,
) -> CentralResultPacket:
    del request
    metrics = extract_result_metrics(raw_result)
    if is_interrupt_result(raw_result):
        packet = build_paused_packet(raw_result, task_context=task_context)
        packet.update(metrics)
        return packet

    text = extract_text(raw_result).strip()
    if not text:
        packet = failed_packet(
            analysis="Deep Agents 未返回有效内容。",
            missing=extract_missing(task_context),
        )
        packet.update(metrics)
        return packet

    parsed = parse_json(text)
    if isinstance(parsed, dict):
        raw_status = str(parsed.get("status", "completed") or "completed").strip().lower()
        if raw_status not in CENTRAL_PACKET_STATUSES:
            raw_status = "completed"

        recommended_action = str(parsed.get("recommended_action", "answer") or "answer").strip().lower()
        if recommended_action == "continue":
            recommended_action = "continue_task"
        if recommended_action not in TASK_RECOMMENDED_ACTIONS:
            recommended_action = "answer"

        missing = [str(item).strip() for item in parsed.get("missing", []) if str(item).strip()]
        risks = [str(item).strip() for item in parsed.get("risks", []) if str(item).strip()][:8]
        pending_review = (
            dict(parsed.get("pending_review", {}) or {})
            if isinstance(parsed.get("pending_review"), dict)
            else {}
        )

        confidence = parsed.get("confidence", 0.0)
        try:
            confidence_value = max(0.0, min(1.0, float(confidence)))
        except Exception:
            confidence_value = 0.0

        control_state, status = map_result_status(
            raw_status=raw_status,
            missing=missing,
            recommended_action=recommended_action,
            pending_review=pending_review,
        )
        packet = {
            "control_state": control_state,
            "status": status,
            "analysis": str(parsed.get("analysis", "") or "").strip() or text,
            "risks": risks,
            "missing": missing,
            "recommended_action": recommended_action,
            "confidence": confidence_value,
            "pending_review": pending_review,
        }
        packet.update(metrics)
        return packet

    packet = {
        "control_state": "completed",
        "status": "done",
        "analysis": text,
        "risks": [],
        "missing": extract_missing(task_context),
        "recommended_action": "answer",
        "confidence": 0.72,
    }
    packet.update(metrics)
    return packet


def extract_result_metrics(raw_result: Any) -> dict[str, Any]:
    if isinstance(raw_result, dict):
        messages = raw_result.get("messages")
        if isinstance(messages, list) and messages:
            return extract_message_metrics(messages[-1])
    return extract_message_metrics(raw_result)


def extract_text(raw_result: Any) -> str:
    if raw_result is None:
        return ""
    if isinstance(raw_result, str):
        return raw_result
    if isinstance(raw_result, dict):
        messages = raw_result.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                return str(last.get("content", "") or "")
            content = getattr(last, "content", "")
            if isinstance(content, list):
                return " ".join(str(item) for item in content if item)
            return str(content or "")
        for key in ("output", "content", "answer", "result"):
            value = raw_result.get(key)
            if value:
                return str(value)
        return json.dumps(raw_result, ensure_ascii=False)
    content = getattr(raw_result, "content", "")
    if isinstance(content, list):
        return " ".join(str(item) for item in content if item)
    if content:
        return str(content)
    return str(raw_result)


def parse_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json\n", "", 1).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def extract_missing(task_context: dict[str, Any] | None) -> list[str]:
    params = task_context or {}
    missing: list[str] = []
    for item in params.get("missing", []) or []:
        text = str(item or "").strip()
        if text and text not in missing:
            missing.append(text)
    return missing


def is_interrupt_result(raw_result: Any) -> bool:
    return isinstance(raw_result, dict) and bool(raw_result.get("__interrupt__"))


def build_paused_packet(
    raw_result: Any,
    *,
    task_context: dict[str, Any] | None,
) -> CentralResultPacket:
    summary = summarize_interrupt(raw_result)
    pending_review = extract_pending_review(raw_result)
    return {
        "control_state": "paused",
        "status": "need_more",
        "analysis": summary or "central 已暂停，等待恢复输入。",
        "risks": [],
        "missing": extract_missing(task_context),
        "recommended_action": "ask_user",
        "confidence": 0.0,
        "pending_review": pending_review,
    }


def map_result_status(
    *,
    raw_status: str,
    missing: list[str],
    recommended_action: str,
    pending_review: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if raw_status == "failed":
        return "stopped", "failed"
    if raw_status == "needs_input" or missing or recommended_action == "ask_user" or pending_review:
        return "paused", "need_more"
    if raw_status == "uncertain" or recommended_action == "continue_task":
        return "completed", "need_more"
    return "completed", "done"


def summarize_interrupt(raw_result: Any) -> str:
    interrupts = raw_result.get("__interrupt__") if isinstance(raw_result, dict) else None
    if not interrupts:
        return ""
    parts: list[str] = []
    for item in interrupts:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            action_requests = value.get("action_requests")
            if isinstance(action_requests, list) and action_requests:
                names = [
                    str(action.get("name", "") or "").strip()
                    for action in action_requests
                    if isinstance(action, dict) and str(action.get("name", "") or "").strip()
                ]
                if names:
                    parts.append(f"等待审批动作：{', '.join(names)}")
                    continue
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value or "").strip()
        text = " ".join(text.split()).strip()
        if text:
            parts.append(text)
    return "；".join(parts[:2])


def extract_pending_review(raw_result: Any) -> dict[str, Any]:
    interrupts = raw_result.get("__interrupt__") if isinstance(raw_result, dict) else None
    if not interrupts:
        return {}
    for item in interrupts:
        value = getattr(item, "value", item)
        if not isinstance(value, dict):
            continue
        action_requests = value.get("action_requests")
        if not isinstance(action_requests, list) or not action_requests:
            continue
        pending_review: dict[str, Any] = {
            "action_requests": [
                dict(action)
                for action in action_requests
                if isinstance(action, dict)
            ]
        }
        review_configs = value.get("review_configs")
        if isinstance(review_configs, list) and review_configs:
            pending_review["review_configs"] = [
                dict(config)
                for config in review_configs
                if isinstance(config, dict)
            ]
        return pending_review
    return {}


def build_resume_value(task_context: dict[str, Any] | None) -> Any | None:
    task = task_context or {}
    if "resume_payload" not in task:
        return None
    payload = task.get("resume_payload")
    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return payload


def failed_packet(
    analysis: str,
    missing: list[str] | None = None,
) -> CentralResultPacket:
    return {
        "control_state": "stopped",
        "status": "failed",
        "analysis": str(analysis or "").strip(),
        "risks": [],
        "missing": list(missing or []),
        "recommended_action": "ask_user" if missing else "answer",
        "confidence": 0.0,
    }


__all__ = [
    "build_paused_packet",
    "build_resume_value",
    "extract_missing",
    "extract_result_metrics",
    "extract_text",
    "failed_packet",
    "is_interrupt_result",
    "map_result_status",
    "normalize_result_packet",
    "parse_json",
]
