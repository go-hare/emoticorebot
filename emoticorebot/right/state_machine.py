"""Compact state helpers for right-brain runs."""

from __future__ import annotations

from enum import StrEnum


class IllegalTransitionError(ValueError):
    """Raised when a right-brain run violates the documented lifecycle."""


class RightBrainState(StrEnum):
    RUNNING = "running"
    DONE = "done"


TERMINAL_STATES = frozenset({RightBrainState.DONE})


class RightBrainStateMachine:
    """Pure transition helpers for the right-brain lifecycle."""

    @staticmethod
    def report_started(state: RightBrainState) -> RightBrainState:
        return _transition(state, {RightBrainState.RUNNING: RightBrainState.RUNNING}, "right.run.started")

    @staticmethod
    def report_progress(state: RightBrainState) -> RightBrainState:
        return _transition(state, {RightBrainState.RUNNING: RightBrainState.RUNNING}, "right.run.progress")

    @staticmethod
    def report_result(state: RightBrainState) -> RightBrainState:
        return _transition(state, {RightBrainState.RUNNING: RightBrainState.DONE}, "right.run.result")

    @staticmethod
    def report_failed(state: RightBrainState) -> RightBrainState:
        return _transition(state, {RightBrainState.RUNNING: RightBrainState.DONE}, "right.run.failed")

    @staticmethod
    def cancel_task(state: RightBrainState) -> RightBrainState:
        return _transition(state, {RightBrainState.RUNNING: RightBrainState.DONE}, "right.run.cancelled")

    @staticmethod
    def archive_task(state: RightBrainState) -> RightBrainState:
        return _transition(state, {RightBrainState.DONE: RightBrainState.DONE}, "right.run.archived")


def _transition(
    state: RightBrainState,
    allowed: dict[RightBrainState, RightBrainState],
    trigger: str,
) -> RightBrainState:
    try:
        return allowed[state]
    except KeyError as exc:
        raise IllegalTransitionError(f"{trigger} cannot transition right brain from {state.value}") from exc


__all__ = ["IllegalTransitionError", "TERMINAL_STATES", "RightBrainState", "RightBrainStateMachine"]
