"""EventType -> payload model contract table for runtime validation."""

from __future__ import annotations

from .commands import (
    ArchiveTaskPayload,
    AssignAgentPayload,
    CancelAgentPayload,
    ControlCommandPayload,
    LeftReplyRequestPayload,
    ResumeAgentPayload,
    RightBrainJobRequestPayload,
    TaskCancelPayload,
    TaskCreatePayload,
    TaskResumePayload,
)
from .events import (
    DeliveryFailedPayload,
    LeftFollowupReadyPayload,
    LeftReplyReadyPayload,
    LeftStreamDeltaPayload,
    PerceptionEventPayload,
    RepliedPayload,
    ReplyReadyPayload,
    RightBrainAcceptedPayload,
    RightBrainProgressPayload,
    RightBrainRejectedPayload,
    RightBrainResultPayload,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    SystemSignalPayload,
    TaskApprovedReportPayload,
    TaskAskPayload,
    TaskCancelledReportPayload,
    TaskEndPayload,
    TaskFailedReportPayload,
    TaskNeedInputReportPayload,
    TaskPlanReadyReportPayload,
    TaskProgressReportPayload,
    TaskRejectedReportPayload,
    TaskResultReportPayload,
    TaskStartedReportPayload,
    TaskSummaryPayload,
    TaskUpdatePayload,
    TurnInputPayload,
)
from .memory_models import MemoryUpdatePayload, MemoryWriteCommittedPayload, MemoryWriteRequestPayload, ReflectSignalPayload
from .task_models import ProtocolModel
from .topics import EventType

PAYLOAD_MODEL_BY_EVENT_TYPE: dict[str, type[ProtocolModel]] = {
    EventType.INPUT_TURN_RECEIVED: TurnInputPayload,
    EventType.INPUT_STREAM_STARTED: StreamStartPayload,
    EventType.INPUT_STREAM_CHUNK: StreamChunkPayload,
    EventType.INPUT_STREAM_COMMITTED: StreamCommitPayload,
    EventType.INPUT_STREAM_INTERRUPTED: StreamInterruptedPayload,
    EventType.LEFT_COMMAND_REPLY_REQUESTED: LeftReplyRequestPayload,
    EventType.LEFT_EVENT_REPLY_READY: LeftReplyReadyPayload,
    EventType.LEFT_EVENT_STREAM_DELTA_READY: LeftStreamDeltaPayload,
    EventType.LEFT_EVENT_FOLLOWUP_READY: LeftFollowupReadyPayload,
    EventType.RIGHT_COMMAND_JOB_REQUESTED: RightBrainJobRequestPayload,
    EventType.RIGHT_EVENT_JOB_ACCEPTED: RightBrainAcceptedPayload,
    EventType.RIGHT_EVENT_PROGRESS: RightBrainProgressPayload,
    EventType.RIGHT_EVENT_JOB_REJECTED: RightBrainRejectedPayload,
    EventType.RIGHT_EVENT_RESULT_READY: RightBrainResultPayload,
    EventType.TASK_CREATE: TaskCreatePayload,
    EventType.TASK_RESUME: TaskResumePayload,
    EventType.TASK_CANCEL: TaskCancelPayload,
    EventType.RUNTIME_ASSIGN_AGENT: AssignAgentPayload,
    EventType.RUNTIME_RESUME_AGENT: ResumeAgentPayload,
    EventType.RUNTIME_CANCEL_AGENT: CancelAgentPayload,
    EventType.RUNTIME_ARCHIVE_TASK: ArchiveTaskPayload,
    EventType.TASK_REPORT_STARTED: TaskStartedReportPayload,
    EventType.TASK_REPORT_PROGRESS: TaskProgressReportPayload,
    EventType.TASK_REPORT_NEED_INPUT: TaskNeedInputReportPayload,
    EventType.TASK_REPORT_PLAN_READY: TaskPlanReadyReportPayload,
    EventType.TASK_REPORT_RESULT: TaskResultReportPayload,
    EventType.TASK_REPORT_APPROVED: TaskApprovedReportPayload,
    EventType.TASK_REPORT_REJECTED: TaskRejectedReportPayload,
    EventType.TASK_REPORT_FAILED: TaskFailedReportPayload,
    EventType.TASK_REPORT_CANCELLED: TaskCancelledReportPayload,
    EventType.TASK_UPDATE: TaskUpdatePayload,
    EventType.TASK_SUMMARY: TaskSummaryPayload,
    EventType.TASK_ASK: TaskAskPayload,
    EventType.TASK_END: TaskEndPayload,
    EventType.REFLECT_LIGHT: ReflectSignalPayload,
    EventType.REFLECT_DEEP: ReflectSignalPayload,
    EventType.OUTPUT_INLINE_READY: ReplyReadyPayload,
    EventType.OUTPUT_PUSH_READY: ReplyReadyPayload,
    EventType.OUTPUT_STREAM_OPEN: ReplyReadyPayload,
    EventType.OUTPUT_STREAM_DELTA: ReplyReadyPayload,
    EventType.OUTPUT_STREAM_CLOSE: ReplyReadyPayload,
    EventType.OUTPUT_REPLIED: RepliedPayload,
    EventType.OUTPUT_DELIVERY_FAILED: DeliveryFailedPayload,
    EventType.MEMORY_WRITE_REQUEST: MemoryWriteRequestPayload,
    EventType.MEMORY_WRITE_COMMITTED: MemoryWriteCommittedPayload,
    EventType.MEMORY_UPDATE_PERSONA: MemoryUpdatePayload,
    EventType.MEMORY_UPDATE_USER_MODEL: MemoryUpdatePayload,
    EventType.CONTROL_SPEAK: ControlCommandPayload,
    EventType.CONTROL_MOVE: ControlCommandPayload,
    EventType.CONTROL_STOP: ControlCommandPayload,
    EventType.CONTROL_MANIPULATE: ControlCommandPayload,
    EventType.PERCEPTION_WAKE_WORD: PerceptionEventPayload,
    EventType.PERCEPTION_VISION_DETECTED: PerceptionEventPayload,
    EventType.PERCEPTION_PROXIMITY_ALERT: PerceptionEventPayload,
    EventType.PERCEPTION_LOCALIZATION_UPDATED: PerceptionEventPayload,
    EventType.SYSTEM_TIMEOUT: SystemSignalPayload,
    EventType.SYSTEM_BACKPRESSURE: SystemSignalPayload,
    EventType.SYSTEM_HEALTH_WARNING: SystemSignalPayload,
    EventType.SYSTEM_WARNING: SystemSignalPayload,
}

KNOWN_EVENT_TYPES = frozenset(str(item) for item in EventType)


def payload_model_for_event(event_type: str) -> type[ProtocolModel] | None:
    return PAYLOAD_MODEL_BY_EVENT_TYPE.get(str(event_type))


def is_known_event_type(event_type: str) -> bool:
    return str(event_type) in KNOWN_EVENT_TYPES


__all__ = [
    "KNOWN_EVENT_TYPES",
    "PAYLOAD_MODEL_BY_EVENT_TYPE",
    "is_known_event_type",
    "payload_model_for_event",
]
