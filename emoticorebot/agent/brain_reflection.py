"""Reflection decision helpers for the brain layer."""

from __future__ import annotations

from typing import Any


def should_deep_reflect(
    *,
    state: dict[str, Any],
    importance: float,
    task: dict[str, Any],
    turn_reflection: dict[str, Any],
) -> tuple[bool, str]:
    brain = state.get("brain")
    execution_review = (
        turn_reflection.get("execution_review")
        if isinstance(turn_reflection, dict) and isinstance(turn_reflection.get("execution_review"), dict)
        else {}
    )
    control_state = str(task.get("control_state", "") or "").strip().lower()
    status = str(task.get("status", "") or "").strip().lower()
    missing = [str(item).strip() for item in list(task.get("missing", []) or []) if str(item).strip()]
    pending_review = task.get("pending_review") if isinstance(task.get("pending_review"), dict) else {}
    effectiveness = str((execution_review or {}).get("effectiveness", "none") or "none").strip().lower()
    failure_reason = str((execution_review or {}).get("main_failure_reason", "") or "").strip()
    user_updates = [str(item).strip() for item in list(turn_reflection.get("user_updates", []) or []) if str(item).strip()]
    soul_updates = [str(item).strip() for item in list(turn_reflection.get("soul_updates", []) or []) if str(item).strip()]
    memory_candidates = list(turn_reflection.get("memory_candidates", []) or []) if isinstance(turn_reflection, dict) else []
    task_reason = str(getattr(brain, "task_reason", "") or "").strip() if brain is not None else ""

    if task.get("invoked") and (status in {"failed", "need_more"} or control_state == "paused"):
        return True, f"brain_task_followup:{control_state or status}"
    if task.get("invoked") and (missing or pending_review):
        return True, "brain_task_blocked_or_waiting_review"
    if task.get("invoked") and effectiveness in {"low", "medium"} and failure_reason:
        return True, f"brain_task_review:{failure_reason}"
    if importance >= 0.82 and (user_updates or soul_updates):
        return True, "brain_high_importance_identity_updates"
    if importance >= 0.82 and memory_candidates:
        return True, "brain_high_importance_memory_candidates"
    if task_reason in {
        "loop_limit_reached",
        "brain_requested_task_followup",
        "task_waiting_for_user_input",
    }:
        return True, f"brain_signal:{task_reason}"
    return False, ""


__all__ = ["should_deep_reflect"]
