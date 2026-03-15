"""Task state machine defined by the v3 architecture document."""

from __future__ import annotations

from enum import StrEnum


class IllegalTransitionError(ValueError):
    """Raised when a task transition violates the runtime state machine."""


class TaskStatus(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    PLANNED = "planned"
    WAITING_INPUT = "waiting_input"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


TERMINAL_STATES = frozenset({TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ARCHIVED})


class TaskStateMachine:
    """Pure transition helpers for the runtime-owned task lifecycle."""

    @staticmethod
    def assign_agent(state: TaskStatus) -> TaskStatus:
        return _transition(
            state,
            {
                TaskStatus.CREATED: TaskStatus.ASSIGNED,
                TaskStatus.PLANNED: TaskStatus.ASSIGNED,
            },
            "runtime.command.assign_agent",
        )

    @staticmethod
    def report_started(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.ASSIGNED: TaskStatus.RUNNING}, "task.report.started")

    @staticmethod
    def report_progress(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.RUNNING: TaskStatus.RUNNING}, "task.report.progress")

    @staticmethod
    def report_plan_ready(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.RUNNING: TaskStatus.PLANNED}, "task.report.plan_ready")

    @staticmethod
    def report_need_input(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.RUNNING: TaskStatus.WAITING_INPUT}, "task.report.need_input")

    @staticmethod
    def report_result(state: TaskStatus, *, review_required: bool) -> TaskStatus:
        if review_required:
            return _transition(state, {TaskStatus.RUNNING: TaskStatus.REVIEWING}, "task.report.result")
        return _transition(state, {TaskStatus.RUNNING: TaskStatus.DONE}, "task.report.result")

    @staticmethod
    def report_failed(state: TaskStatus) -> TaskStatus:
        allowed = {
            TaskStatus.RUNNING: TaskStatus.FAILED,
            TaskStatus.REVIEWING: TaskStatus.FAILED,
            TaskStatus.WAITING_INPUT: TaskStatus.FAILED,
        }
        return _transition(state, allowed, "task.report.failed")

    @staticmethod
    def resume_task(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.WAITING_INPUT: TaskStatus.ASSIGNED}, "brain.command.resume_task")

    @staticmethod
    def timeout_waiting_input(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.WAITING_INPUT: TaskStatus.FAILED}, "system.signal.timeout")

    @staticmethod
    def report_approved(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.REVIEWING: TaskStatus.DONE}, "task.report.approved")

    @staticmethod
    def report_rejected(state: TaskStatus) -> TaskStatus:
        return _transition(state, {TaskStatus.REVIEWING: TaskStatus.ASSIGNED}, "task.report.rejected")

    @staticmethod
    def cancel_task(state: TaskStatus) -> TaskStatus:
        allowed = {
            TaskStatus.CREATED: TaskStatus.CANCELLED,
            TaskStatus.ASSIGNED: TaskStatus.CANCELLED,
            TaskStatus.RUNNING: TaskStatus.CANCELLED,
            TaskStatus.PLANNED: TaskStatus.CANCELLED,
            TaskStatus.WAITING_INPUT: TaskStatus.CANCELLED,
            TaskStatus.REVIEWING: TaskStatus.CANCELLED,
        }
        return _transition(state, allowed, "brain.command.cancel_task")

    @staticmethod
    def archive_task(state: TaskStatus) -> TaskStatus:
        allowed = {
            TaskStatus.DONE: TaskStatus.ARCHIVED,
            TaskStatus.FAILED: TaskStatus.ARCHIVED,
            TaskStatus.CANCELLED: TaskStatus.ARCHIVED,
        }
        return _transition(state, allowed, "runtime.command.archive_task")


def _transition(state: TaskStatus, allowed: dict[TaskStatus, TaskStatus], trigger: str) -> TaskStatus:
    try:
        return allowed[state]
    except KeyError as exc:
        raise IllegalTransitionError(f"{trigger} cannot transition task from {state.value}") from exc


__all__ = ["IllegalTransitionError", "TERMINAL_STATES", "TaskStateMachine", "TaskStatus"]
