from __future__ import annotations

import pytest

from emoticorebot.runtime.state_machine import IllegalTransitionError, TaskState, TaskStateMachine


def test_simple_task_completes_without_review() -> None:
    state = TaskState.RUNNING
    state = TaskStateMachine.report_started(state)
    state = TaskStateMachine.report_progress(state)
    state = TaskStateMachine.report_result(state, review_required=False)
    state = TaskStateMachine.archive_task(state)

    assert state is TaskState.DONE


def test_review_flow_stays_running_until_reviewer_finishes() -> None:
    state = TaskState.RUNNING
    state = TaskStateMachine.report_started(state)
    state = TaskStateMachine.report_result(state, review_required=True)

    assert state is TaskState.RUNNING

    state = TaskStateMachine.report_rejected(state)
    assert state is TaskState.RUNNING


def test_waiting_resumes_to_running_per_document() -> None:
    state = TaskState.RUNNING
    state = TaskStateMachine.report_started(state)
    state = TaskStateMachine.report_need_input(state)

    assert state is TaskState.WAITING
    assert TaskStateMachine.resume_task(state) is TaskState.RUNNING


def test_cancel_from_running_is_allowed() -> None:
    assert TaskStateMachine.cancel_task(TaskState.RUNNING) is TaskState.DONE


def test_legacy_intermediate_and_result_states_are_not_task_states() -> None:
    values = {member.value for member in TaskState}

    assert "blocked_input" not in values
    assert "idle" not in values
    assert "assigned" not in values
    assert "planned" not in values
    assert "reviewing" not in values
    assert "failed" not in values
    assert "cancelled" not in values
    assert "archived" not in values


def test_illegal_transition_raises() -> None:
    with pytest.raises(IllegalTransitionError):
        TaskStateMachine.report_need_input(TaskState.DONE)
