"""Decision packet schema and normalization helpers for the brain layer."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from emoticorebot.protocol.task_models import TaskSpec

BrainFinalDecision = Literal["", "answer", "ask_user", "continue"]
BrainTaskAction = Literal["", "none", "create_task", "fill_task"]


class BrainControlPacket(TypedDict, total=False):
    """Structured brain output used by decision and narration layers."""

    task_action: BrainTaskAction
    task_reason: str
    final_decision: BrainFinalDecision
    final_message: str
    task_brief: str
    task: TaskSpec
    intent: str
    working_hypothesis: str
    notify_user: bool
    execution_summary: str
    retrieval_query: str
    retrieval_focus: list[str]
    retrieved_memory_ids: list[str]
    message_id: str
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def normalize_task_spec(payload: Any, actual: dict[str, Any] | None = None) -> TaskSpec:
    """Normalize task spec and prefer real runtime-generated task fields."""
    model_task = payload if isinstance(payload, dict) else {}
    source = dict(actual or {})

    def _pick(key: str) -> Any:
        if key in source and source.get(key) not in (None, "", []):
            return source.get(key)
        return model_task.get(key)

    task: TaskSpec = {}
    text_fields = (
        "task_id",
        "origin_message_id",
        "title",
        "request",
        "goal",
        "expected_output",
        "history_context",
        "channel",
        "chat_id",
        "session_id",
    )
    for key in text_fields:
        value = str(_pick(key) or "").strip()
        if value:
            task[key] = value

    list_fields = ("constraints", "success_criteria", "memory_bundle_ids", "skill_hints", "media")
    for key in list_fields:
        values = normalize_str_list(_pick(key))
        if values:
            task[key] = values

    history_value = _pick("history")
    if isinstance(history_value, list):
        task["history"] = [dict(item) for item in history_value if isinstance(item, dict)]

    task_context_value = _pick("task_context")
    if isinstance(task_context_value, dict) and task_context_value:
        task["task_context"] = dict(task_context_value)

    if "task_id" not in task:
        raise RuntimeError("BrainControlPacket.task.task_id must not be empty")
    return task


def normalize_brain_packet(payload: Any, *, current_context: dict[str, Any]) -> BrainControlPacket:
    """Validate a structured brain packet and enforce task-action consistency."""
    if not isinstance(payload, dict):
        raise RuntimeError("Brain agent did not return a structured BrainControlPacket")

    packet: BrainControlPacket = {
        "message_id": str(payload.get("message_id", "") or current_context.get("message_id", "") or "").strip(),
        "intent": str(payload.get("intent", "") or "").strip(),
        "working_hypothesis": str(payload.get("working_hypothesis", "") or "").strip(),
        "task_action": str(payload.get("task_action", "none") or "none").strip(),
        "task_reason": str(payload.get("task_reason", "") or "").strip(),
        "final_decision": str(payload.get("final_decision", "answer") or "answer").strip(),
        "final_message": str(payload.get("final_message", "") or "").strip(),
        "task_brief": str(payload.get("task_brief", "") or "").strip(),
        "execution_summary": str(payload.get("execution_summary", "") or "").strip(),
        "notify_user": bool(payload.get("notify_user", True)),
        "retrieval_query": str(payload.get("retrieval_query", "") or "").strip(),
        "retrieval_focus": normalize_str_list(payload.get("retrieval_focus")),
        "retrieved_memory_ids": normalize_str_list(payload.get("retrieved_memory_ids")),
    }

    for key in ("model_name", "prompt_tokens", "completion_tokens", "total_tokens"):
        if key in payload and payload.get(key) not in (None, ""):
            packet[key] = payload.get(key)

    if packet["task_action"] not in {"none", "create_task", "fill_task"}:
        raise RuntimeError(f"Invalid brain task_action: {packet['task_action']!r}")
    if packet["final_decision"] not in {"answer", "ask_user", "continue"}:
        raise RuntimeError(f"Invalid brain final_decision: {packet['final_decision']!r}")
    if not packet["final_message"]:
        raise RuntimeError("BrainControlPacket.final_message must not be empty")

    tool_action = str(current_context.get("tool_action", "none") or "none").strip()
    actual_task_spec = current_context.get("task_spec")
    if tool_action != "none" and packet["task_action"] != tool_action:
        raise RuntimeError(
            f"BrainControlPacket.task_action={packet['task_action']!r} does not match actual tool action {tool_action!r}"
        )
    if tool_action == "create_task" and packet["final_decision"] != "continue":
        raise RuntimeError("BrainControlPacket.final_decision must be 'continue' after create_task")
    if tool_action == "fill_task" and packet["final_decision"] != "continue":
        raise RuntimeError("BrainControlPacket.final_decision must be 'continue' after fill_task")

    model_task = payload.get("task")
    if actual_task_spec is not None:
        packet["task"] = normalize_task_spec(model_task, actual_task_spec)
    elif isinstance(model_task, dict) and model_task:
        packet["task"] = normalize_task_spec(model_task)

    if packet["task_action"] in {"create_task", "fill_task"} and "task" not in packet:
        raise RuntimeError("BrainControlPacket.task is required when task_action is create_task or fill_task")

    return packet


# ---------------------------------------------------------------------------
# Raw JSON parsing – used when the brain agent outputs plain text JSON
# instead of structured_response.
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def parse_raw_brain_json(result: dict[str, Any]) -> dict[str, Any]:
    """Extract and parse a JSON ``BrainControlPacket`` from an agent result.

    The function first tries ``result["structured_response"]`` (backward-compat).
    If that is *None*, it falls back to extracting the last AI message's text
    content, strips optional markdown code fences, and parses the JSON.

    Raises ``RuntimeError`` when the text cannot be parsed.
    """
    # Fast path: structured output still present
    structured = result.get("structured_response")
    if isinstance(structured, dict):
        return structured

    # Locate the last AI message text
    messages = result.get("messages", [])
    text = ""
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content is None:
            content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, list):
            parts = [
                str(item.get("text", "")) if isinstance(item, dict) and item.get("type") == "text" else str(item)
                for item in content
            ]
            content = "\n".join(parts)
        if isinstance(content, str) and content.strip():
            text = content.strip()
            break

    if not text:
        raise RuntimeError("Brain agent returned empty content; cannot parse BrainControlPacket JSON")

    # Strip markdown ```json ... ``` fences if present
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Brain agent output is not valid JSON: {exc}\n---\n{text[:500]}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"Brain agent JSON is not an object: {type(payload).__name__}")

    return payload


__all__ = [
    "BrainControlPacket",
    "BrainFinalDecision",
    "BrainTaskAction",
    "normalize_brain_packet",
    "normalize_str_list",
    "normalize_task_spec",
    "parse_raw_brain_json",
]
