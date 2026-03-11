"""Task lifecycle state definitions for the task-system boundary."""

from __future__ import annotations

from typing import Literal

TaskMode = Literal["sync", "async"]
TaskControlState = Literal["idle", "running", "paused", "stopped", "completed"]
TaskStatus = Literal["none", "done", "need_more", "failed"]
CentralPacketStatus = Literal["completed", "needs_input", "uncertain", "failed"]
TaskRecommendedAction = Literal["", "answer", "ask_user", "continue_task"]

TASK_MODES = {"sync", "async"}
TASK_CONTROL_STATES = {"idle", "running", "paused", "stopped", "completed"}
TASK_STATUSES = {"none", "done", "need_more", "failed"}
CENTRAL_PACKET_STATUSES = {"completed", "needs_input", "uncertain", "failed"}
TASK_RECOMMENDED_ACTIONS = {"", "answer", "ask_user", "continue_task"}

__all__ = [
    "CentralPacketStatus",
    "CENTRAL_PACKET_STATUSES",
    "TaskControlState",
    "TASK_CONTROL_STATES",
    "TaskMode",
    "TASK_MODES",
    "TaskRecommendedAction",
    "TASK_RECOMMENDED_ACTIONS",
    "TaskStatus",
    "TASK_STATUSES",
]
