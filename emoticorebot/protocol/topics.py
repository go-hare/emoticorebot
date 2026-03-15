"""Stable topic and event type constants for the v3 runtime protocol."""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    INPUT_EVENT = "input.event"
    BRAIN_COMMAND = "brain.command"
    RUNTIME_COMMAND = "runtime.command"
    TASK_REPORT = "task.report"
    TASK_EVENT = "task.event"
    OUTPUT_EVENT = "output.event"
    MEMORY_SIGNAL = "memory.signal"
    SAFETY_EVENT = "safety.event"
    CONTROL_COMMAND = "control.command"
    PERCEPTION_EVENT = "perception.event"
    SYSTEM_SIGNAL = "system.signal"


class EventType(StrEnum):
    INPUT_USER_MESSAGE = "input.event.user_message"
    INPUT_INTERRUPT = "input.event.interrupt"
    INPUT_VOICE_CHUNK = "input.event.voice_chunk"
    INPUT_CHANNEL_ATTACHMENT = "input.event.channel_attachment"

    BRAIN_CREATE_TASK = "brain.command.create_task"
    BRAIN_CANCEL_TASK = "brain.command.cancel_task"
    BRAIN_RESUME_TASK = "brain.command.resume_task"
    BRAIN_REPLY = "brain.command.reply"
    BRAIN_ASK_USER = "brain.command.ask_user"

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

    TASK_EVENT_CREATED = "task.event.created"
    TASK_EVENT_ASSIGNED = "task.event.assigned"
    TASK_EVENT_STARTED = "task.event.started"
    TASK_EVENT_PROGRESS = "task.event.progress"
    TASK_EVENT_NEED_INPUT = "task.event.need_input"
    TASK_EVENT_PLANNED = "task.event.planned"
    TASK_EVENT_RESULT = "task.event.result"
    TASK_EVENT_REVIEWING = "task.event.reviewing"
    TASK_EVENT_APPROVED = "task.event.approved"
    TASK_EVENT_REJECTED = "task.event.rejected"
    TASK_EVENT_FAILED = "task.event.failed"
    TASK_EVENT_CANCELLED = "task.event.cancelled"

    OUTPUT_REPLY_READY = "output.event.reply_ready"
    OUTPUT_REPLY_APPROVED = "output.event.reply_approved"
    OUTPUT_REPLY_REDACTED = "output.event.reply_redacted"
    OUTPUT_REPLY_BLOCKED = "output.event.reply_blocked"
    OUTPUT_REPLIED = "output.event.replied"
    OUTPUT_DELIVERY_FAILED = "output.event.delivery_failed"

    MEMORY_REFLECT_TURN = "memory.signal.reflect_turn"
    MEMORY_REFLECT_DEEP = "memory.signal.reflect_deep"
    MEMORY_WRITE_REQUEST = "memory.signal.write_request"
    MEMORY_WRITE_COMMITTED = "memory.signal.write_committed"
    MEMORY_UPDATE_PERSONA = "memory.signal.update_persona"
    MEMORY_UPDATE_USER_MODEL = "memory.signal.update_user_model"

    SAFETY_ALLOWED = "safety.event.allowed"
    SAFETY_REDACTED = "safety.event.redacted"
    SAFETY_BLOCKED = "safety.event.blocked"
    SAFETY_WARNING = "safety.event.warning"

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
