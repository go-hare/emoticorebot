from __future__ import annotations

import pytest

from emoticorebot.runtime.state_machine import IllegalTransitionError, TaskStateMachine, TaskStatus


def test_simple_task_completes_without_review() -> None:
    state = TaskStatus.CREATED
    state = TaskStateMachine.assign_agent(state)
    state = TaskStateMachine.report_started(state)
    state = TaskStateMachine.report_progress(state)
    state = TaskStateMachine.report_result(state, review_required=False)
    state = TaskStateMachine.archive_task(state)

    assert state is TaskStatus.ARCHIVED


def test_review_flow_returns_to_assigned_when_rejected() -> None:
    state = TaskStatus.CREATED
    state = TaskStateMachine.assign_agent(state)
    state = TaskStateMachine.report_started(state)
    state = TaskStateMachine.report_result(state, review_required=True)

    assert state is TaskStatus.REVIEWING

    state = TaskStateMachine.report_rejected(state)
    assert state is TaskStatus.ASSIGNED


def test_waiting_input_resumes_to_assigned_per_document() -> None:
    state = TaskStatus.CREATED
    state = TaskStateMachine.assign_agent(state)
    state = TaskStateMachine.report_started(state)
    state = TaskStateMachine.report_need_input(state)

    assert state is TaskStatus.WAITING_INPUT
    assert TaskStateMachine.resume_task(state) is TaskStatus.ASSIGNED


def test_cancel_from_assigned_is_allowed() -> None:
    state = TaskStateMachine.assign_agent(TaskStatus.CREATED)
    assert TaskStateMachine.cancel_task(state) is TaskStatus.CANCELLED


def test_blocked_input_and_idle_are_not_task_states() -> None:
    values = {member.value for member in TaskStatus}

    assert "blocked_input" not in values
    assert "idle" not in values


def test_illegal_transition_raises() -> None:
    with pytest.raises(IllegalTransitionError):
        TaskStateMachine.report_started(TaskStatus.CREATED)
