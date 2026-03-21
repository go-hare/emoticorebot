"""Topic and event type constants for the v3 runtime protocol."""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    INPUT_EVENT = "input.event"
    BRAIN_COMMAND = "brain.command"
    BRAIN_EVENT = "brain.event"
    EXECUTOR_COMMAND = "executor.command"
    EXECUTOR_EVENT = "executor.event"
    REFLECTION_EVENT = "reflection.event"
    REFLECTION_SIGNAL = "reflection.signal"
    OUTPUT_EVENT = "output.event"
    CONTROL_COMMAND = "control.command"
    PERCEPTION_EVENT = "perception.event"
    SYSTEM_SIGNAL = "system.signal"


class EventType(StrEnum):
    INPUT_TURN_RECEIVED = "input.event.turn_received"
    INPUT_STREAM_STARTED = "input.event.stream_started"
    INPUT_STREAM_CHUNK = "input.event.stream_chunk"
    INPUT_STREAM_COMMITTED = "input.event.stream_committed"
    INPUT_STREAM_INTERRUPTED = "input.event.stream_interrupted"

    BRAIN_COMMAND_REPLY_REQUESTED = "brain.command.reply_requested"
    BRAIN_EVENT_REPLY_READY = "brain.event.reply_ready"
    BRAIN_EVENT_STREAM_DELTA_READY = "brain.event.stream_delta_ready"

    EXECUTOR_COMMAND_JOB_REQUESTED = "executor.command.job_requested"
    EXECUTOR_EVENT_JOB_REJECTED = "executor.event.job_rejected"
    EXECUTOR_EVENT_RESULT_READY = "executor.event.result_ready"

    REFLECTION_LIGHT = "reflection.event.light"

    OUTPUT_INLINE_READY = "output.event.inline_ready"
    OUTPUT_PUSH_READY = "output.event.push_ready"
    OUTPUT_STREAM_OPEN = "output.event.stream_open"
    OUTPUT_STREAM_DELTA = "output.event.stream_delta"
    OUTPUT_STREAM_CLOSE = "output.event.stream_close"
    OUTPUT_REPLIED = "output.event.replied"
    OUTPUT_DELIVERY_FAILED = "output.event.delivery_failed"

    REFLECTION_WRITE_REQUEST = "reflection.signal.write_request"
    REFLECTION_WRITE_COMMITTED = "reflection.signal.write_committed"
    REFLECTION_UPDATE_PERSONA = "reflection.signal.update_persona"
    REFLECTION_UPDATE_USER_MODEL = "reflection.signal.update_user_model"

    CONTROL_SPEAK = "control.command.speak"
    CONTROL_MOVE = "control.command.move"
    CONTROL_STOP = "control.command.stop"
    CONTROL_MANIPULATE = "control.command.manipulate"

    PERCEPTION_WAKE_WORD = "perception.event.wake_word"
    PERCEPTION_VISION_DETECTED = "perception.event.vision_detected"
    PERCEPTION_PROXIMITY_ALERT = "perception.event.proximity_alert"
    PERCEPTION_LOCALIZATION_UPDATED = "perception.event.localization_updated"

    SYSTEM_TIMEOUT = "system.signal.timeout"
    SYSTEM_BACKPRESSURE = "system.signal.backpressure"
    SYSTEM_HEALTH_WARNING = "system.signal.health_warning"
    SYSTEM_WARNING = "system.signal.warning"


def topic_for(event_type: EventType | str) -> str:
    parts = str(event_type).split(".")
    if len(parts) < 3:
        raise ValueError(f"invalid event type: {event_type!r}")
    return ".".join(parts[:2])


__all__ = ["EventType", "Topic", "topic_for"]
