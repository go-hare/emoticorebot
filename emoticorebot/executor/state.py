"""Minimal executor state definitions."""

from __future__ import annotations

from enum import StrEnum


class ExecutorState(StrEnum):
    RUNNING = "running"
    DONE = "done"


TERMINAL_STATES = frozenset({ExecutorState.DONE})
__all__ = ["TERMINAL_STATES", "ExecutorState"]
