"""Topic and event type constants for the v3 runtime protocol."""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    INPUT_EVENT = "input.event"
    INTENT_EVENT = "intent.event"
    LEFT_COMMAND = "left.command"
    LEFT_EVENT = "left.event"
    RIGHT_COMMAND = "right.command"
    RIGHT_EVENT = "right.event"
    REFLECTION_EVENT = "reflection.event"
    RUNTIME_COMMAND = "runtime.command"
    TASK_COMMAND = "task.command"
    TASK_REPORT = "task.report"
    TASK_EVENT = "task.event"
    OUTPUT_EVENT = "output.event"
    MEMORY_SIGNAL = "memory.signal"
    CONTROL_COMMAND = "control.command"
    PERCEPTION_EVENT = "perception.event"
    SYSTEM_SIGNAL = "system.signal"


class EventType(StrEnum):
    INPUT_TURN_RECEIVED = "input.event.turn_received"
    INPUT_STREAM_STARTED = "input.event.stream_started"
    INPUT_STREAM_CHUNK = "input.event.stream_chunk"
    INPUT_STREAM_COMMITTED = "input.event.stream_committed"
    INPUT_STREAM_INTERRUPTED = "input.event.stream_interrupted"

    INTENT_SCORED = "intent.event.scored"

    LEFT_COMMAND_REPLY_REQUESTED = "left.command.reply_requested"
    LEFT_EVENT_REPLY_READY = "left.event.reply_ready"
    LEFT_EVENT_STREAM_DELTA_READY = "left.event.stream_delta_ready"
    LEFT_EVENT_FOLLOWUP_READY = "left.event.followup_ready"

    RIGHT_COMMAND_JOB_REQUESTED = "right.command.job_requested"
    RIGHT_EVENT_JOB_ACCEPTED = "right.event.job_accepted"
    RIGHT_EVENT_JOB_CLARIFY = "right.event.job_clarify"
    RIGHT_EVENT_JOB_REJECTED = "right.event.job_rejected"
    RIGHT_EVENT_RESULT_READY = "right.event.result_ready"

    TASK_CREATE = "task.command.create"
    TASK_RESUME = "task.command.resume"
    TASK_CANCEL = "task.command.cancel"

    RUNTIME_ASSIGN_AGENT = "runtime.command.assign_agent"
    RUNTIME_RESUME_AGENT = "runtime.command.resume_agent"
    RUNTIME_CANCEL_AGENT = "runtime.command.cancel_agent"
    RUNTIME_ARCHIVE_TASK = "runtime.command.archive_task"

    TASK_REPORT_STARTED = "task.report.started"
    TASK_REPORT_PROGRESS = "task.report.progress"
    TASK_REPORT_NEED_INPUT = "task.report.need_input"
    TASK_REPORT_PLAN_READY = "task.report.plan_ready"
    TASK_REPORT_RESULT = "task.report.result"
    TASK_REPORT_APPROVED = "task.report.approved"
    TASK_REPORT_REJECTED = "task.report.rejected"
    TASK_REPORT_FAILED = "task.report.failed"
    TASK_REPORT_CANCELLED = "task.report.cancelled"

    TASK_UPDATE = "task.event.update"
    TASK_SUMMARY = "task.event.summary"
    TASK_ASK = "task.event.ask"
    TASK_END = "task.event.end"

    REFLECT_LIGHT = "reflection.event.light"
    REFLECT_DEEP = "reflection.event.deep"

    OUTPUT_INLINE_READY = "output.event.inline_ready"
    OUTPUT_PUSH_READY = "output.event.push_ready"
    OUTPUT_STREAM_OPEN = "output.event.stream_open"
    OUTPUT_STREAM_DELTA = "output.event.stream_delta"
    OUTPUT_STREAM_CLOSE = "output.event.stream_close"
    OUTPUT_REPLIED = "output.event.replied"
    OUTPUT_DELIVERY_FAILED = "output.event.delivery_failed"

    MEMORY_WRITE_REQUEST = "memory.signal.write_request"
    MEMORY_WRITE_COMMITTED = "memory.signal.write_committed"
    MEMORY_UPDATE_PERSONA = "memory.signal.update_persona"
    MEMORY_UPDATE_USER_MODEL = "memory.signal.update_user_model"

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
