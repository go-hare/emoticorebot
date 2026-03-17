"""Priority model for the v3 runtime bus."""

from __future__ import annotations

from enum import IntEnum

from .topics import EventType


class EventPriority(IntEnum):
    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4


PRIORITY_BY_EVENT_TYPE: dict[str, EventPriority] = {
    EventType.CONTROL_STOP: EventPriority.P0,
    EventType.TASK_CANCEL: EventPriority.P0,
    EventType.INPUT_STABLE: EventPriority.P1,
    EventType.TASK_CREATE: EventPriority.P1,
    EventType.TASK_RESUME: EventPriority.P1,
    EventType.TASK_ASK: EventPriority.P1,
    EventType.TASK_END: EventPriority.P2,
    EventType.OUTPUT_REPLY_READY: EventPriority.P2,
    EventType.TASK_REPORT_PROGRESS: EventPriority.P3,
    EventType.TASK_UPDATE: EventPriority.P3,
    EventType.TASK_SUMMARY: EventPriority.P3,
    EventType.REFLECT_LIGHT: EventPriority.P4,
    EventType.REFLECT_DEEP: EventPriority.P4,
    EventType.MEMORY_WRITE_REQUEST: EventPriority.P4,
}


def priority_for(event_type: EventType | str) -> EventPriority:
    return PRIORITY_BY_EVENT_TYPE.get(str(event_type), EventPriority.P3)


__all__ = ["EventPriority", "PRIORITY_BY_EVENT_TYPE", "priority_for"]
