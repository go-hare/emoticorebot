"""Helpers for one-round central execution results."""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from emoticorebot.utils.llm_utils import extract_message_metrics

ROUND_STATUSES = {"done", "need_input", "continue", "failed"}


class CentralRoundResult(TypedDict, total=False):
    status: Literal["done", "need_input", "continue", "failed"]
    analysis: str
    missing: list[str]
    risks: list[str]
    confidence: float
    pending_review: dict[str, Any]
    thread_id: str
    run_id: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def normalize_round_result(
    raw_result: Any,
    *,
    task_context: dict[str, Any] | None,
    thread_id: str,
    run_id: str,
) -> CentralRoundResult:
    metrics = extract_result_metrics(raw_result)
    if is_interrupt_result(raw_result):
        result = {
            "status": "need_input",
            "analysis": summarize_interrupt(raw_result) or "central 已暂停，等待更多输入。",
            "missing": extract_missing(task_context),
            "risks": [],
            "confidence": 0.0,
            "pending_review": extract_pending_review(raw_result),
            "thread_id": thread_id,
            "run_id": run_id,
        }
        result.update(metrics)
        return result

    text = extract_text(raw_result).strip()
    if not text:
        result = failed_result("central 未返回有效内容。")
        result["thread_id"] = thread_id
        result["run_id"] = run_id
        result.update(metrics)
        return result

    parsed = parse_json(text)
    if isinstance(parsed, dict):
        status = normalize_status(parsed, task_context)
        result: CentralRoundResult = {
            "status": status,
            "analysis": str(parsed.get("analysis", "") or text).strip(),
            "missing": [str(item).strip() for item in list(parsed.get("missing", []) or []) if str(item).strip()],
            "risks": [str(item).strip() for item in list(parsed.get("risks", []) or []) if str(item).strip()][:8],
            "confidence": clamp_confidence(parsed.get("confidence", 0.0)),
            "pending_review": dict(parsed.get("pending_review", {}) or {}) if isinstance(parsed.get("pending_review"), dict) else {},
            "thread_id": thread_id,
            "run_id": run_id,
        }
        result.update(metrics)
        return result

    result = {
        "status": "done",
        "analysis": text,
        "missing": [],
        "risks": [],
        "confidence": 0.72,
        "pending_review": {},
        "thread_id": thread_id,
        "run_id": run_id,
    }
    result.update(metrics)
    return result


def normalize_status(parsed: dict[str, Any], task_context: dict[str, Any] | None) -> str:
    raw_status = str(parsed.get("status", "") or "").strip().lower()
    if raw_status in {"completed", "done"}:
        return "done"
    if raw_status in {"needs_input", "need_input"}:
        return "need_input"
    if raw_status in {"uncertain", "continue", "continue_task"}:
        return "continue"
    if raw_status == "failed":
        return "failed"

    recommended_action = str(parsed.get("recommended_action", "") or "").strip().lower()
    if recommended_action == "ask_user" or parsed.get("pending_review") or parsed.get("missing"):
        return "need_input"
    if recommended_action in {"continue", "continue_task"}:
        return "continue"
    if extract_missing(task_context):
        return "need_input"
    return "done"


def should_request_input(result: CentralRoundResult) -> bool:
    return str(result.get("status", "") or "") == "need_input"


def should_continue(result: CentralRoundResult) -> bool:
    return str(result.get("status", "") or "") == "continue"


def should_notify_stage(result: CentralRoundResult, *, previous: str) -> bool:
    status = str(result.get("status", "") or "")
    analysis = str(result.get("analysis", "") or "").strip()
    if not analysis:
        return False
    if status in {"need_input", "failed"}:
        return True
    if analysis == str(previous or "").strip():
        return False
    return status == "continue"


def build_stage_payload(result: CentralRoundResult) -> dict[str, Any]:
    return {
        "status": str(result.get("status", "") or ""),
        "missing": list(result.get("missing", []) or []),
        "risks": list(result.get("risks", []) or []),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "thread_id": str(result.get("thread_id", "") or ""),
        "run_id": str(result.get("run_id", "") or ""),
    }


def build_input_question(result: CentralRoundResult) -> str:
    text = str(result.get("analysis", "") or "").strip()
    if text:
        return text
    missing = [str(item).strip() for item in list(result.get("missing", []) or []) if str(item).strip()]
    if len(missing) == 1:
        return f"继续执行前，我需要你补充：{missing[0]}"
    if missing:
        return "继续执行前，我需要你补充这些信息：" + "、".join(missing[:3])
    return "继续执行前，我需要你补充一些信息。"


def pick_input_field(result: CentralRoundResult) -> str:
    missing = [str(item).strip() for item in list(result.get("missing", []) or []) if str(item).strip()]
    if missing:
        return missing[0]
    if result.get("pending_review"):
        return "review"
    return "details"


def build_resume_request(*, field: str, answer: str) -> str:
    field_text = str(field or "").strip() or "detail"
    answer_text = str(answer or "").strip()
    return f"用户补充了 {field_text}: {answer_text}"


def build_followup_request(result: CentralRoundResult, *, previous: str) -> str:
    analysis = str(result.get("analysis", "") or "").strip()
    if analysis:
        return f"继续推进当前任务。上一轮结论：{analysis}"
    text = str(previous or "").strip()
    return f"继续推进当前任务：{text}" if text else "继续推进当前任务。"


def build_round_limit_summary(result: CentralRoundResult, *, rounds: int) -> str:
    summary = str(result.get("analysis", "") or "").strip()
    suffix = f"已达到内部执行轮数上限（{rounds} 轮），请主 agent 决定下一步。"
    return f"{summary} {suffix}".strip() if summary else suffix


def merge_task_context(task_context: dict[str, Any], result: CentralRoundResult) -> dict[str, Any]:
    merged = dict(task_context or {})
    merged["summary"] = str(result.get("analysis", "") or merged.get("summary", "") or "").strip()
    merged["missing"] = [
        str(item).strip()
        for item in list(result.get("missing", []) or merged.get("missing", []) or [])
        if str(item).strip()
    ]
    merged["pending_review"] = dict(result.get("pending_review", {}) or merged.get("pending_review", {}) or {})
    merged["thread_id"] = str(result.get("thread_id", "") or merged.get("thread_id", "") or "").strip()
    merged["run_id"] = str(result.get("run_id", "") or merged.get("run_id", "") or "").strip()
    return merged


def normalize_trace_event(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = dict(event or {})
    content = str(payload.get("content", "") or "").strip()
    if not content:
        return None
    return {
        "event": str(payload.get("event", "") or payload.get("phase", "") or "task.trace").strip(),
        "content": content,
        "phase": str(payload.get("phase", "") or "trace").strip(),
        "producer": "central",
        "payload": payload,
    }


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


def failed_result(message: str) -> CentralRoundResult:
    return {
        "status": "failed",
        "analysis": str(message or "").strip(),
        "missing": [],
        "risks": [],
        "confidence": 0.0,
        "pending_review": {},
    }


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


def summarize_interrupt(raw_result: Any) -> str:
    interrupts = raw_result.get("__interrupt__") if isinstance(raw_result, dict) else None
    if not interrupts:
        return ""
    parts: list[str] = []
    for item in interrupts:
        value = getattr(item, "value", item)
        text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, dict) else str(value or "").strip()
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
        if isinstance(value, dict):
            return dict(value)
    return {}


def clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


__all__ = [
    "CentralRoundResult",
    "build_followup_request",
    "build_input_question",
    "build_resume_request",
    "build_resume_value",
    "build_round_limit_summary",
    "build_stage_payload",
    "extract_missing",
    "extract_result_metrics",
    "extract_text",
    "failed_result",
    "merge_task_context",
    "normalize_round_result",
    "normalize_trace_event",
    "pick_input_field",
    "should_continue",
    "should_notify_stage",
    "should_request_input",
]
