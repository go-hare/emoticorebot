"""EventType -> payload model contract table for runtime validation."""

from __future__ import annotations

from .commands import (
    ControlCommandPayload,
    LeftReplyRequestPayload,
    RightBrainJobRequestPayload,
)
from .events import (
    DeliveryFailedPayload,
    LeftFollowupReadyPayload,
    LeftReplyReadyPayload,
    LeftStreamDeltaPayload,
    OutputInlineReadyPayload,
    OutputPushReadyPayload,
    OutputStreamClosePayload,
    OutputStreamDeltaPayload,
    OutputStreamOpenPayload,
    PerceptionEventPayload,
    RepliedPayload,
    RightBrainAcceptedPayload,
    RightBrainProgressPayload,
    RightBrainRejectedPayload,
    RightBrainResultPayload,
    StreamChunkPayload,
    StreamCommitPayload,
    StreamInterruptedPayload,
    StreamStartPayload,
    SystemSignalPayload,
    TurnInputPayload,
)
from .reflection_models import ReflectionUpdatePayload, ReflectionWriteCommittedPayload, ReflectionWriteRequestPayload, ReflectionSignalPayload
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
    EventType.REFLECTION_LIGHT: ReflectionSignalPayload,
    EventType.REFLECTION_DEEP: ReflectionSignalPayload,
    EventType.OUTPUT_INLINE_READY: OutputInlineReadyPayload,
    EventType.OUTPUT_PUSH_READY: OutputPushReadyPayload,
    EventType.OUTPUT_STREAM_OPEN: OutputStreamOpenPayload,
    EventType.OUTPUT_STREAM_DELTA: OutputStreamDeltaPayload,
    EventType.OUTPUT_STREAM_CLOSE: OutputStreamClosePayload,
    EventType.OUTPUT_REPLIED: RepliedPayload,
    EventType.OUTPUT_DELIVERY_FAILED: DeliveryFailedPayload,
    EventType.REFLECTION_WRITE_REQUEST: ReflectionWriteRequestPayload,
    EventType.REFLECTION_WRITE_COMMITTED: ReflectionWriteCommittedPayload,
    EventType.REFLECTION_UPDATE_PERSONA: ReflectionUpdatePayload,
    EventType.REFLECTION_UPDATE_USER_MODEL: ReflectionUpdatePayload,
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


