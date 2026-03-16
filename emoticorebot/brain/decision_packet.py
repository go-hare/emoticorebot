"""Decision packet schema and normalization helpers for the brain layer."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from emoticorebot.protocol.task_models import TaskSpec

BrainFinalDecision = Literal["", "answer", "ask_user", "continue"]
BrainTaskAction = Literal["", "none", "create_task", "resume_task", "cancel_task"]


class BrainControlPacket(TypedDict, total=False):
    """Structured brain output used by decision and narration layers.

    Core fields are intentionally minimal:
    - ``task_action``
    - ``final_decision``
    - ``final_message``

    Other fields are optional hints. ``task`` may also be omitted when the
    runtime can derive it from current context, for example using the user's
    raw message as ``request`` for ``create_task``.

    Recommended minimal shapes:
    - ``none``: only the 3 core fields
    - ``create_task``: only the 3 core fields
    - ``resume_task`` / ``cancel_task``: the 3 core fields + ``task.task_id``
    """

    task_action: BrainTaskAction
    task_reason: str
    final_decision: BrainFinalDecision
    final_message: str
    task: TaskSpec


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
    """Normalize task spec for v3 brain packets."""
    model_task = payload if isinstance(payload, dict) else {}
    source = dict(actual or {})

    def _pick(key: str) -> Any:
        if key in source and source.get(key) not in (None, "", []):
            return source.get(key)
        return model_task.get(key)

    task: TaskSpec = {}
    text_fields = (
        "task_id",
        "title",
        "request",
        "goal",
        "expected_output",
        "history_context",
        "review_policy",
        "preferred_agent",
        "reason",
    )
    for key in text_fields:
        value = str(_pick(key) or "").strip()
        if value:
            task[key] = value

    list_fields = ("constraints", "success_criteria", "memory_refs", "skill_hints")
    for key in list_fields:
        values = normalize_str_list(_pick(key))
        if values:
            task[key] = values

    return task


def normalize_brain_packet(payload: Any, *, current_context: dict[str, Any]) -> BrainControlPacket:
    """Validate a structured brain packet for the v3 executive brain."""
    if not isinstance(payload, dict):
        raise RuntimeError("Brain agent did not return a structured BrainControlPacket")

    packet: BrainControlPacket = {
        "task_action": str(payload.get("task_action", "none") or "none").strip(),
        "task_reason": str(payload.get("task_reason", "") or "").strip(),
        "final_decision": str(payload.get("final_decision", "answer") or "answer").strip(),
        "final_message": str(payload.get("final_message", "") or "").strip(),
    }

    if packet["task_action"] not in {"none", "create_task", "resume_task", "cancel_task"}:
        raise RuntimeError(f"Invalid brain task_action: {packet['task_action']!r}")
    if packet["final_decision"] not in {"answer", "ask_user", "continue"}:
        raise RuntimeError(f"Invalid brain final_decision: {packet['final_decision']!r}")

    model_task = payload.get("task")
    if packet["task_action"] == "create_task":
        packet["task"] = normalize_task_spec(
            model_task,
            {
                "request": str(current_context.get("user_input", "") or "").strip(),
                "history_context": str(current_context.get("history_context", "") or "").strip(),
                "review_policy": str(current_context.get("review_policy", "") or "").strip(),
                "preferred_agent": str(current_context.get("preferred_agent", "") or "").strip(),
            },
        )
        if not str(packet["task"].get("request", "") or "").strip():
            raise RuntimeError("BrainControlPacket.task.request must not be empty for create_task")
    elif packet["task_action"] in {"resume_task", "cancel_task"}:
        packet["task"] = normalize_task_spec(
            model_task,
            {
                "task_id": str(
                    current_context.get("waiting_task_id", "")
                    or current_context.get("active_task_id", "")
                    or current_context.get("latest_task_id", "")
                    or ""
                ).strip(),
                "reason": str(payload.get("task_reason", "") or "").strip(),
            },
        )
        if not str(packet["task"].get("task_id", "") or "").strip():
            raise RuntimeError(f"BrainControlPacket.task.task_id must not be empty for {packet['task_action']}")

    if packet["task_action"] in {"create_task", "resume_task", "cancel_task"} and "task" not in packet:
        raise RuntimeError(f"BrainControlPacket.task is required when task_action is {packet['task_action']}")

    return packet


# ---------------------------------------------------------------------------
# Raw JSON parsing – used when the brain agent outputs plain text JSON
# instead of structured_response.
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)
_TAG_SECTION_RE = re.compile(r"^\s*####([a-zA-Z_]+)####\s*$", re.MULTILINE)


def _looks_like_brain_packet(payload: Any) -> bool:
    return isinstance(payload, dict) and "task_action" in payload and "final_decision" in payload and "final_message" in payload


def _extract_brain_text(result: Any) -> str:
    if isinstance(result, str):
        result = {"messages": [result]}
    elif not isinstance(result, dict):
        result = {"messages": [result]}

    structured = result.get("structured_response")
    if _looks_like_brain_packet(structured):
        return json.dumps(structured, ensure_ascii=False)

    messages = result.get("messages", [])
    for msg in reversed(messages):
        if _looks_like_brain_packet(msg):
            return json.dumps(msg, ensure_ascii=False)
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
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
            return content.strip()
    return ""


def _parse_tagged_brain_text(text: str) -> dict[str, Any] | None:
    matches = list(_TAG_SECTION_RE.finditer(text))
    if not matches:
        return None

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = str(match.group(1) or "").strip().lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections[name] = body

    user_text = str(sections.get("user", "") or "").strip()
    task_text = str(sections.get("task", "") or "").strip()
    if not user_text:
        raise RuntimeError("Brain tagged output requires a non-empty ####user#### section")
    if not task_text:
        raise RuntimeError("Brain tagged output requires a non-empty ####task#### section")

    task_fields: dict[str, str] = {}
    for raw in task_text.splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            raise RuntimeError(f"Invalid brain task line: {line!r}")
        key = key.strip().lower()
        value = value.strip()
        if key:
            task_fields[key] = value

    action = str(task_fields.get("action", task_fields.get("task_action", "none")) or "none").strip()
    decision = str(task_fields.get("mode", task_fields.get("final_decision", "")) or "").strip()
    if not decision:
        decision = "continue" if action != "none" else "answer"

    payload: dict[str, Any] = {
        "task_action": action,
        "final_decision": decision,
        "final_message": user_text,
    }

    task: dict[str, Any] = {}
    text_fields = (
        "task_id",
        "title",
        "request",
        "goal",
        "expected_output",
        "history_context",
        "review_policy",
        "preferred_agent",
        "reason",
    )
    list_fields = ("constraints", "success_criteria", "memory_refs", "skill_hints")

    for key in text_fields:
        value = str(task_fields.get(key, "") or "").strip()
        if value:
            task[key] = value

    for key in list_fields:
        raw_value = str(task_fields.get(key, "") or "").strip()
        if not raw_value:
            continue
        if raw_value.startswith("["):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                parsed = None
            values = normalize_str_list(parsed)
            if values:
                task[key] = values
                continue
        values = [item.strip() for item in raw_value.split("|") if item.strip()]
        if values:
            task[key] = values

    if task:
        payload["task"] = task
    return payload


def parse_raw_brain_json(result: Any) -> dict[str, Any]:
    """Extract a ``BrainControlPacket`` from an agent result.

    The function first tries ``result["structured_response"]`` (backward-compat).
    If that is *None*, it falls back to extracting the last AI message's text
    content. It supports:
    - plain JSON objects
    - tagged output blocks: ``####user####`` and ``####task####``

    Raises ``RuntimeError`` when the text cannot be parsed.
    """
    if _looks_like_brain_packet(result):
        return result

    text = _extract_brain_text(result)
    if not text:
        raise RuntimeError("Brain agent returned empty content; cannot parse BrainControlPacket")

    tagged_payload = _parse_tagged_brain_text(text)
    if tagged_payload is not None:
        return tagged_payload

    # Strip markdown ```json ... ``` fences if present
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Brain agent output is neither tagged text nor valid JSON: {exc}\n---\n{text[:500]}") from exc

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
