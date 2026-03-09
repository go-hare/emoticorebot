"""Routing decisions for the turn graph."""

from __future__ import annotations

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class TurnRouter:
    """Choose the next node for the main-brain/executor loop."""

    def __init__(self, max_executor_attempts: int = 3):
        self.max_executor_attempts = max_executor_attempts

    def route_next(self, state: dict) -> str:
        done: bool = state.get("done", False)
        executor = state.get("executor", {})

        executor_request: str = _get(executor, "request", "")
        executor_control_state: str = _get(executor, "control_state", "")
        executor_status: str = _get(executor, "status", "")
        executor_attempts: int = _get(executor, "attempts", 0)

        if done:
            return "memory"

        if executor_attempts >= self.max_executor_attempts:
            return "main_brain"

        if executor_request and executor_control_state == "running":
            return "executor"

        if executor_status in {"done", "need_more", "failed"}:
            return "main_brain"

        return "memory"


__all__ = ["TurnRouter"]
