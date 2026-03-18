from __future__ import annotations

import pytest

from emoticorebot.right.state_machine import IllegalTransitionError, RightBrainState, RightBrainStateMachine


def test_right_brain_run_completes_to_done() -> None:
    state = RightBrainState.RUNNING
    state = RightBrainStateMachine.report_started(state)
    state = RightBrainStateMachine.report_progress(state)
    state = RightBrainStateMachine.report_result(state)
    state = RightBrainStateMachine.archive_task(state)

    assert state is RightBrainState.DONE


def test_cancel_from_running_is_allowed() -> None:
    assert RightBrainStateMachine.cancel_task(RightBrainState.RUNNING) is RightBrainState.DONE


def test_state_machine_only_exposes_running_and_done() -> None:
    values = {member.value for member in RightBrainState}

    assert values == {"running", "done"}


def test_illegal_transition_raises() -> None:
    with pytest.raises(IllegalTransitionError):
        RightBrainStateMachine.report_progress(RightBrainState.DONE)
