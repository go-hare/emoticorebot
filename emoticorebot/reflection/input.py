"""Build normalized reflection inputs from runtime payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from emoticorebot.types import EmotionState, ExecutionInfo, MainBrainDecisionPacket, ReflectionInput

_SOURCE_TYPES = {"user_turn", "task_event", "internal_task_event"}
_EXECUTION_STATUSES = {"none", "done", "failed", "running", "partial", "completed"}


def build_reflection_input(payload: Mapping[str, Any] | None) -> ReflectionInput:
    data = dict(payload or {})
    metadata = _normalize_dict(data.get("metadata"))
    message_id = _as_text(metadata.get("message_id") or data.get("message_id"))
    output = _as_text(data.get("assistant_output") or data.get("output"))

    task = _normalize_task_state(data.get("task") or metadata.get("task"))
    task_trace = _normalize_trace_items(data.get("task_trace") or task.get("task_trace"))

    reflection_input: ReflectionInput = {
        "turn_id": _as_text(data.get("turn_id")) or (f"turn_{message_id}" if message_id else ""),
        "message_id": message_id,
        "session_id": _as_text(data.get("session_id")),
        "source_type": _normalize_source_type(data.get("source_type")),
        "user_input": _as_text(data.get("user_input")),
        "output": output,
        "assistant_output": output,
        "channel": _as_text(data.get("channel") or metadata.get("channel")),
        "chat_id": _as_text(data.get("chat_id") or metadata.get("chat_id")),
        "main_brain": _normalize_main_brain_decision_packet(data.get("main_brain")),
        "task": task,
        "task_trace": task_trace,
        "metadata": metadata,
    }

    emotion = _normalize_emotion_state(data.get("emotion"))
    if emotion:
        reflection_input["emotion"] = emotion

    execution = _normalize_execution_info(data.get("execution"))
    if execution is None:
        execution = _build_execution_info(data, metadata, task)
    if execution is not None:
        reflection_input["execution"] = execution

    return _prune_empty(reflection_input)


def _normalize_source_type(value: Any) -> str:
    source_type = _as_text(value).lower()
    return source_type if source_type in _SOURCE_TYPES else "user_turn"


def _normalize_main_brain_decision_packet(value: Any) -> MainBrainDecisionPacket:
    payload = _normalize_dict(value)
    if not payload:
        return {}

    normalized: MainBrainDecisionPacket = {}

    for key in (
        "intent",
        "working_hypothesis",
        "task_reason",
        "final_message",
        "execution_summary",
        "retrieval_query",
        "message_id",
        "model_name",
    ):
        text = _as_text(payload.get(key))
        if text:
            normalized[key] = text

    task_action = _as_text(payload.get("task_action")) or "none"
    if task_action in {"none", "create_task", "cancel_task"}:
        normalized["task_action"] = task_action

    task_mode = _as_text(payload.get("task_mode")) or "skip"
    if task_mode in {"skip", "sync", "async"}:
        normalized["task_mode"] = task_mode

    task = _normalize_dict(payload.get("task"))
    if task:
        normalized["task"] = task

    retrieval_focus = _normalize_str_list(payload.get("retrieval_focus"))
    if retrieval_focus:
        normalized["retrieval_focus"] = retrieval_focus

    retrieved_memory_ids = _normalize_str_list(payload.get("retrieved_memory_ids"))
    if retrieved_memory_ids:
        normalized["retrieved_memory_ids"] = retrieved_memory_ids

    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            value_int = int(payload.get(key))
        except Exception:
            continue
        if value_int >= 0:
            normalized[key] = value_int

    return normalized


def _normalize_emotion_state(value: Any) -> EmotionState:
    payload = _normalize_dict(value)
    if not payload:
        return {}

    normalized: EmotionState = {}
    emotion_label = _as_text(payload.get("emotion_label"))
    if emotion_label:
        normalized["emotion_label"] = emotion_label

    pad = _normalize_float_map(payload.get("pad"), keys=("pleasure", "arousal", "dominance"))
    if pad:
        normalized["pad"] = pad

    drives = _normalize_float_map(payload.get("drives"), keys=("social", "energy"))
    if drives:
        normalized["drives"] = drives

    return normalized


def _normalize_execution_info(value: Any) -> ExecutionInfo | None:
    payload = _normalize_dict(value)
    if not payload:
        return None

    status = _as_text(payload.get("status")).lower() or "none"
    if status not in _EXECUTION_STATUSES:
        status = "none"

    summary = _as_text(payload.get("summary"))
    failure_reason = _as_text(payload.get("failure_reason"))

    invoked = bool(payload.get("invoked", True))
    if not invoked and not any((summary, failure_reason)):
        return {
            "invoked": False,
            "status": "none",
            "summary": "",
            "failure_reason": "",
        }

    return {
        "invoked": invoked,
        "status": status,
        "summary": summary,
        "failure_reason": failure_reason,
    }


def _build_execution_info(
    data: dict[str, Any],
    metadata: dict[str, Any],
    task: dict[str, Any],
) -> ExecutionInfo | None:
    execution_metadata = _normalize_dict(metadata.get("execution"))
    execution_summary = _as_text(data.get("execution_summary")) or _as_text(execution_metadata.get("summary"))
    main_brain = _normalize_dict(data.get("main_brain"))
    task_action = _as_text(execution_metadata.get("task_action")) or _as_text(main_brain.get("task_action"))

    status = _normalize_execution_status(
        state=_as_text(task.get("state")),
        result=_as_text(task.get("result")),
    )
    if status == "none":
        status = _normalize_execution_status(
            state=_as_text(execution_metadata.get("status")),
            result=_as_text(execution_metadata.get("result_status")),
        )

    failure_reason = _as_text(task.get("error"))

    summary = execution_summary or _as_text(task.get("summary")) or _as_text(task.get("analysis"))

    invoked = bool(task) or bool(execution_summary) or task_action in {"create_task", "cancel_task"}
    if not invoked and status == "none":
        return None

    if status == "none" and invoked and task:
        status = "running"
    elif status == "none" and invoked:
        status = "done"

    return {
        "invoked": invoked,
        "status": status or "none",
        "summary": summary,
        "failure_reason": failure_reason,
    }


def _normalize_execution_status(*, state: str, result: str) -> str:
    lifecycle = _as_text(state).lower()
    result = _as_text(result).lower()

    if lifecycle == "failed" or result == "failed":
        return "failed"
    if lifecycle == "done" and result == "cancelled":
        return "failed"
    if result == "partial":
        return "partial"
    if lifecycle == "done":
        return "done"
    if lifecycle in {"running", "completed"}:
        return lifecycle
    if result == "success":
        return "done"
    return "none"


def _normalize_task_state(value: Any) -> dict[str, Any]:
    payload = _normalize_dict(value)
    if not payload:
        return {}

    normalized: dict[str, Any] = {}

    if "invoked" in payload:
        normalized["invoked"] = bool(payload.get("invoked"))

    for key in (
        "task_id",
        "title",
        "state",
        "result",
        "summary",
        "analysis",
        "error",
    ):
        text = _as_text(payload.get(key))
        if text:
            normalized[key] = text

    task_trace = _normalize_trace_items(payload.get("task_trace"))
    if task_trace:
        normalized["task_trace"] = task_trace

    params = _normalize_dict(payload.get("params"))
    if params:
        normalized["params"] = params

    return normalized


def _normalize_trace_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalize_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _as_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _normalize_float_map(value: Any, *, keys: tuple[str, ...]) -> dict[str, float]:
    payload = _normalize_dict(value)
    if not payload:
        return {}
    normalized: dict[str, float] = {}
    for key in keys:
        if key not in payload:
            continue
        try:
            numeric = float(payload.get(key))
        except Exception:
            continue
        normalized[key] = numeric
    return normalized


def _prune_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in ("", None, [], {})}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


__all__ = ["build_reflection_input"]

