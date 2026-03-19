"""Minimal right-brain state definitions."""

from __future__ import annotations

from enum import StrEnum


class RightBrainState(StrEnum):
    RUNNING = "running"
    DONE = "done"


TERMINAL_STATES = frozenset({RightBrainState.DONE})
__all__ = ["TERMINAL_STATES", "RightBrainState"]
