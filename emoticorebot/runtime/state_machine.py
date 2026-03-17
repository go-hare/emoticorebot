"""Task state machine defined by the v3 architecture document."""

from __future__ import annotations

from enum import StrEnum


class IllegalTransitionError(ValueError):
    """Raised when a task transition violates the runtime state machine."""


class TaskState(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"


TERMINAL_STATES = frozenset({TaskState.DONE})


class TaskStateMachine:
    """Pure transition helpers for the compact runtime-owned task lifecycle."""

    @staticmethod
    def report_started(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.RUNNING: TaskState.RUNNING}, "task.report.started")

    @staticmethod
    def report_progress(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.RUNNING: TaskState.RUNNING}, "task.report.progress")

    @staticmethod
    def report_plan_ready(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.RUNNING: TaskState.RUNNING}, "task.report.plan_ready")

    @staticmethod
    def report_need_input(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.RUNNING: TaskState.WAITING}, "task.report.need_input")

    @staticmethod
    def report_result(state: TaskState, *, review_required: bool) -> TaskState:
        target = TaskState.RUNNING if review_required else TaskState.DONE
        return _transition(state, {TaskState.RUNNING: target}, "task.report.result")

    @staticmethod
    def report_failed(state: TaskState) -> TaskState:
        allowed = {
            TaskState.RUNNING: TaskState.DONE,
            TaskState.WAITING: TaskState.DONE,
        }
        return _transition(state, allowed, "task.report.failed")

    @staticmethod
    def resume_task(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.WAITING: TaskState.RUNNING}, "brain.command.resume_task")

    @staticmethod
    def timeout_waiting_input(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.WAITING: TaskState.DONE}, "system.signal.timeout")

    @staticmethod
    def report_approved(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.RUNNING: TaskState.DONE}, "task.report.approved")

    @staticmethod
    def report_rejected(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.RUNNING: TaskState.RUNNING}, "task.report.rejected")

    @staticmethod
    def cancel_task(state: TaskState) -> TaskState:
        allowed = {
            TaskState.RUNNING: TaskState.DONE,
            TaskState.WAITING: TaskState.DONE,
        }
        return _transition(state, allowed, "brain.command.cancel_task")

    @staticmethod
    def archive_task(state: TaskState) -> TaskState:
        return _transition(state, {TaskState.DONE: TaskState.DONE}, "runtime.command.archive_task")


def _transition(state: TaskState, allowed: dict[TaskState, TaskState], trigger: str) -> TaskState:
    try:
        return allowed[state]
    except KeyError as exc:
        raise IllegalTransitionError(f"{trigger} cannot transition task from {state.value}") from exc


__all__ = ["IllegalTransitionError", "TERMINAL_STATES", "TaskState", "TaskStateMachine"]
