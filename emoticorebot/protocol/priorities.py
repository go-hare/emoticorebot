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
    EventType.INPUT_TURN_RECEIVED: EventPriority.P1,
    EventType.INPUT_STREAM_STARTED: EventPriority.P1,
    EventType.INPUT_STREAM_CHUNK: EventPriority.P1,
    EventType.INPUT_STREAM_COMMITTED: EventPriority.P1,
    EventType.INPUT_STREAM_INTERRUPTED: EventPriority.P0,
    EventType.BRAIN_COMMAND_REPLY_REQUESTED: EventPriority.P1,
    EventType.BRAIN_EVENT_REPLY_READY: EventPriority.P1,
    EventType.BRAIN_EVENT_STREAM_DELTA_READY: EventPriority.P1,
    EventType.EXECUTOR_COMMAND_JOB_REQUESTED: EventPriority.P1,
    EventType.EXECUTOR_EVENT_JOB_REJECTED: EventPriority.P2,
    EventType.EXECUTOR_EVENT_RESULT_READY: EventPriority.P2,
    EventType.OUTPUT_INLINE_READY: EventPriority.P2,
    EventType.OUTPUT_PUSH_READY: EventPriority.P2,
    EventType.OUTPUT_STREAM_OPEN: EventPriority.P2,
    EventType.OUTPUT_STREAM_DELTA: EventPriority.P2,
    EventType.OUTPUT_STREAM_CLOSE: EventPriority.P2,
    EventType.REFLECTION_LIGHT: EventPriority.P4,
    EventType.REFLECTION_WRITE_REQUEST: EventPriority.P4,
}


def priority_for(event_type: EventType | str) -> EventPriority:
    return PRIORITY_BY_EVENT_TYPE.get(str(event_type), EventPriority.P3)


__all__ = ["EventPriority", "PRIORITY_BY_EVENT_TYPE", "priority_for"]
