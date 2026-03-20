"""Minimal main-brain decision packet helpers."""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

from emoticorebot.protocol.contracts import TaskMode

TaskAction = Literal["", "none", "create_task", "cancel_task"]


class DecisionPacket(TypedDict, total=False):
    task_action: TaskAction
    task_mode: TaskMode
    task_reason: str
    final_message: str
    task: dict[str, Any]


def normalize_decision_packet(payload: Any, *, current_context: dict[str, Any]) -> DecisionPacket:
    if not isinstance(payload, dict):
        raise RuntimeError("Main-brain model did not return a structured DecisionPacket")

    packet: DecisionPacket = {
        "task_action": str(payload.get("task_action", "none") or "none").strip(),
        "task_mode": str(payload.get("task_mode", "") or "").strip(),
        "task_reason": str(payload.get("task_reason", "") or payload.get("reason", "") or "").strip(),
        "final_message": str(payload.get("final_message", "") or "").strip(),
    }

    if packet["task_action"] not in {"none", "create_task", "cancel_task"}:
        raise RuntimeError(f"Invalid task_action: {packet['task_action']!r}")
    if packet["task_mode"] not in {"skip", "sync", "async"}:
        raise RuntimeError(f"Invalid task_mode: {packet['task_mode']!r}")
    if packet["task_action"] == "none" and packet["task_mode"] != "skip":
        raise RuntimeError("task_action=none requires task_mode=skip")
    if packet["task_action"] in {"create_task", "cancel_task"} and packet["task_mode"] not in {"sync", "async"}:
        raise RuntimeError("create_task/cancel_task require task_mode=sync|async")

    if packet["task_action"] == "cancel_task":
        task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        task_id = str(
            task_payload.get("task_id", "")
            or current_context.get("active_task_id", "")
            or current_context.get("latest_task_id", "")
            or ""
        ).strip()
        if not task_id:
            raise RuntimeError("DecisionPacket cancel_task requires an active task_id")
        packet["task"] = {"task_id": task_id}

    return packet


_TAG_SECTION_RE = re.compile(r"^\s*####([a-zA-Z_]+)####\s*$", re.MULTILINE)
_SUPPORTED_TASK_FIELDS = {"action", "task_mode", "task_id", "reason"}


def _looks_like_decision_packet(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and "task_action" in payload
        and "task_mode" in payload
        and "final_message" in payload
    )


def _extract_response_text(result: Any) -> str:
    if isinstance(result, str):
        result = {"messages": [result]}
    elif not isinstance(result, dict):
        result = {"messages": [result]}

    messages = result.get("messages", [])
    for msg in reversed(messages):
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


def _parse_tagged_output(text: str) -> dict[str, Any] | None:
    matches = list(_TAG_SECTION_RE.finditer(text))
    if not matches:
        return None

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        name = str(match.group(1) or "").strip().lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append((name, body))

    if [name for name, _body in sections] != ["user", "task"]:
        raise RuntimeError("Main-brain output must place ####user#### before ####task####")

    user_text = str(sections[0][1] or "").strip()
    task_text = str(sections[1][1] or "").strip()
    if not user_text:
        raise RuntimeError("Main-brain tagged output requires a non-empty ####user#### section")
    if not task_text:
        raise RuntimeError("Main-brain tagged output requires a non-empty ####task#### section")

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
            raise RuntimeError(f"Invalid main-brain task line: {line!r}")
        key = key.strip().lower()
        value = value.strip()
        if key not in _SUPPORTED_TASK_FIELDS:
            raise RuntimeError(f"Unsupported main-brain task field: {key!r}")
        task_fields[key] = value

    payload: dict[str, Any] = {
        "task_action": str(task_fields.get("action", "") or "none").strip(),
        "task_mode": str(task_fields.get("task_mode", "") or "").strip(),
        "final_message": user_text,
    }
    reason = str(task_fields.get("reason", "") or "").strip()
    if reason:
        payload["task_reason"] = reason
    task_id = str(task_fields.get("task_id", "") or "").strip()
    if task_id:
        payload["task"] = {"task_id": task_id}
    return payload


def parse_decision_packet(result: Any) -> dict[str, Any]:
    if _looks_like_decision_packet(result):
        return result
    if isinstance(result, dict):
        structured = result.get("structured_response")
        if _looks_like_decision_packet(structured):
            return structured
        for msg in list(result.get("messages", []) or []):
            if _looks_like_decision_packet(msg):
                return msg

    text = _extract_response_text(result)
    if not text:
        raise RuntimeError("Main-brain model returned empty content; cannot parse DecisionPacket")

    tagged_payload = _parse_tagged_output(text)
    if tagged_payload is None:
        raise RuntimeError("Main-brain output must contain non-empty ####user#### and ####task#### blocks")
    return tagged_payload


__all__ = ["DecisionPacket", "TaskAction", "normalize_decision_packet", "parse_decision_packet"]
