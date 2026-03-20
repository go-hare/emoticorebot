"""Minimal execution state definitions."""

from __future__ import annotations

from enum import StrEnum


class ExecutionState(StrEnum):
    RUNNING = "running"
    DONE = "done"


TERMINAL_STATES = frozenset({ExecutionState.DONE})

__all__ = ["ExecutionState", "TERMINAL_STATES"]
