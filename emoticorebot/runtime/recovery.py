"""Recovery helpers for the v3 runtime scheduler."""

from __future__ import annotations

from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.runtime.state_machine import TERMINAL_STATES, TaskState

from .assignment import AssignmentFactory
from .task_store import RuntimeTaskRecord


class RecoveryPlanner:
    """Plans runtime self-commands for terminal tasks."""

    def __init__(self, assignment_factory: AssignmentFactory | None = None) -> None:
        self._assignments = assignment_factory or AssignmentFactory()

    def plan_archive(self, task: RuntimeTaskRecord, *, reason: str | None = None) -> list[BusEnvelope[ProtocolModel]]:
        if task.state not in TERMINAL_STATES:
            return []
        return [self._assignments.build_archive_task(task=task, reason=reason)]


__all__ = ["RecoveryPlanner"]
