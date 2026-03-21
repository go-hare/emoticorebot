"""Brain decision packet helpers for the action-based runtime protocol."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict

from emoticorebot.world_model.schema import normalize_mainline, normalize_stage, normalize_string_list

BrainActionType = Literal["none", "execute", "reflect"]
ExecuteOperation = Literal["run", "cancel"]


class BrainAction(TypedDict, total=False):
    type: BrainActionType
    operation: ExecuteOperation
    task_id: str
    goal: str
    mainline: list[Any]
    current_stage: str | list[str]
    current_checks: list[str]
    reason: str
    mode: Literal["turn"]


class DecisionPacket(TypedDict, total=False):
    """Structured brain output used by the runtime.

    The brain model only decides:
    - what to say to the user (`final_message`)
    - which internal actions should run (`actions`)
    """

    final_message: str
    actions: list[BrainAction]


def normalize_decision_packet(payload: Any, *, current_context: dict[str, Any]) -> DecisionPacket:
    """Validate an action-based decision packet for the brain runtime."""
    if not isinstance(payload, dict):
        raise RuntimeError("Brain model did not return a structured DecisionPacket")

    packet: DecisionPacket = {
        "final_message": str(payload.get("final_message", "") or "").strip(),
        "actions": _normalize_actions(payload.get("actions", payload.get("action")), current_context=current_context),
    }
    return packet


_TAG_SECTION_RE = re.compile(r"^\s*#{4,}([a-zA-Z_]+)#{4,}\s*$", re.MULTILINE)


def _normalize_actions(value: Any, *, current_context: dict[str, Any]) -> list[BrainAction]:
    if value in (None, "", []):
        return [{"type": "none"}]
    if isinstance(value, dict):
        raw_actions = [value]
    elif isinstance(value, list):
        raw_actions = list(value)
    else:
        raise RuntimeError("DecisionPacket actions must be a JSON object or array")

    normalized: list[BrainAction] = []
    reflect_count = 0
    execute_count = 0
    for item in raw_actions:
        if not isinstance(item, dict):
            raise RuntimeError("Each brain action must be a JSON object")
        action_type = str(item.get("type", "") or "").strip().lower()
        if action_type not in {"none", "execute", "reflect"}:
            raise RuntimeError(f"Invalid action type: {action_type!r}")
        if action_type == "none":
            normalized.append({"type": "none"})
            continue
        if action_type == "reflect":
            reflect_count += 1
            mode = str(item.get("mode", "") or "turn").strip().lower()
            if mode != "turn":
                raise RuntimeError("reflect actions currently require mode='turn'")
            normalized.append({"type": "reflect", "mode": "turn"})
            continue

        operation = str(item.get("operation", "") or "run").strip().lower()
        if operation not in {"run", "cancel"}:
            raise RuntimeError(f"Invalid execute operation: {operation!r}")
        execute_count += 1
        task_id = str(item.get("task_id", "") or "").strip()
        if operation == "cancel":
            task_id = task_id or str(current_context.get("current_task_id", "") or "").strip()
            if not task_id:
                raise RuntimeError("execute cancel requires an active task_id")
            action: BrainAction = {
                "type": "execute",
                "operation": "cancel",
                "task_id": task_id,
            }
            reason = str(item.get("reason", "") or "").strip()
            if reason:
                action["reason"] = reason
            normalized.append(action)
            continue

        current_task_id = str(current_context.get("current_task_id", "") or "").strip()
        task_id = task_id or current_task_id or "new"
        goal = str(item.get("goal", "") or "").strip()
        mainline = normalize_mainline(item.get("mainline"))
        current_stage = normalize_stage(item.get("current_stage"))
        current_checks = normalize_string_list(item.get("current_checks"))
        if not current_checks:
            one_check = str(item.get("current_check", "") or "").strip()
            if one_check:
                current_checks = [one_check]
        if task_id == "new" and not goal:
            raise RuntimeError("new execute actions require goal")
        if not current_checks:
            raise RuntimeError("execute actions require current_checks")
        action = {
            "type": "execute",
            "operation": "run",
            "task_id": task_id,
            "current_checks": current_checks,
        }
        if goal:
            action["goal"] = goal
        if mainline:
            action["mainline"] = mainline
        if current_stage not in (None, "", []):
            action["current_stage"] = current_stage
        reason = str(item.get("reason", "") or "").strip()
        if reason:
            action["reason"] = reason
        normalized.append(action)

    if not normalized:
        return [{"type": "none"}]
    if any(item["type"] == "none" for item in normalized) and len(normalized) > 1:
        raise RuntimeError("action type 'none' cannot be combined with other actions")
    if reflect_count > 1:
        raise RuntimeError("DecisionPacket currently supports at most one reflect action")
    if execute_count > 1:
        raise RuntimeError("DecisionPacket single-task mode supports at most one execute action")
    return normalized


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


def _strip_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


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

    section_names = [name for name, _body in sections]
    if section_names == ["user"]:
        raise RuntimeError("Brain output must contain non-empty #####user###### and #####Action###### blocks")
    if section_names != ["user", "action"]:
        raise RuntimeError("Brain output must place #####user###### before #####Action######")

    user_text = str(sections[0][1] or "").strip()
    action_text = _strip_json_fence(sections[1][1])
    if not user_text:
        raise RuntimeError("Brain tagged output requires a non-empty #####user###### section")
    if not action_text:
        raise RuntimeError("Brain tagged output requires a non-empty #####Action###### section")
    try:
        actions = json.loads(action_text)
    except Exception as exc:
        raise RuntimeError("Brain action block must be valid JSON") from exc
    return {
        "final_message": user_text,
        "actions": actions,
    }


def parse_decision_packet(result: Any) -> dict[str, Any]:
    """Extract a DecisionPacket from strict tagged brain output."""
    text = _extract_response_text(result)
    if not text:
        raise RuntimeError("Brain model returned empty content; cannot parse DecisionPacket")

    tagged_payload = _parse_tagged_output(text)
    if tagged_payload is not None:
        return tagged_payload

    raise RuntimeError("Brain output must contain non-empty #####user###### and #####Action###### blocks")


__all__ = [
    "BrainAction",
    "BrainActionType",
    "DecisionPacket",
    "ExecuteOperation",
    "normalize_decision_packet",
    "parse_decision_packet",
]
