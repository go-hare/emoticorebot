"""Envelope model for the v3 priority pub/sub bus."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import Field, model_validator

from .priorities import EventPriority, priority_for
from .task_models import ProtocolModel
from .topics import Topic, topic_for

PayloadT = TypeVar("PayloadT", bound=ProtocolModel)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class BusEnvelope(ProtocolModel, Generic[PayloadT]):
    event_id: str = Field(default_factory=lambda: _prefixed_id("evt"))
    topic: str
    event_type: str
    priority: int
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    source: str
    target: str
    correlation_id: str | None = None
    causation_id: str | None = None
    emitted_at: str = Field(default_factory=_utc_now)
    dedupe_key: str | None = None
    payload: PayloadT

    @model_validator(mode="after")
    def validate_envelope(self) -> "BusEnvelope[PayloadT]":
        from .event_contracts import is_known_event_type, payload_model_for_event

        if self.priority < EventPriority.P0 or self.priority > EventPriority.P4:
            raise ValueError("priority must be between 0 and 4")
        expected_topic = topic_for(self.event_type)
        if self.topic != expected_topic:
            raise ValueError(f"topic {self.topic!r} does not match event type {self.event_type!r}")
        expected_payload_model = payload_model_for_event(self.event_type)
        if is_known_event_type(self.event_type) and expected_payload_model is None:
            raise ValueError(f"event payload contract is missing for {self.event_type!r}")
        if expected_payload_model is not None and not isinstance(self.payload, expected_payload_model):
            raise ValueError(
                f"payload {self.payload.__class__.__name__!r} does not match "
                f"expected {expected_payload_model.__name__!r} for {self.event_type!r}"
            )
        if expected_topic != Topic.INPUT_EVENT and expected_topic != Topic.SYSTEM_SIGNAL and not self.session_id:
            raise ValueError("session_id is required for business-path events")
        return self


def build_envelope(
    *,
    event_type: str,
    source: str,
    target: str,
    payload: PayloadT,
    priority: EventPriority | int | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    task_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    dedupe_key: str | None = None,
) -> BusEnvelope[PayloadT]:
    resolved_priority = int(priority_for(event_type) if priority is None else priority)
    return BusEnvelope(
        topic=topic_for(event_type),
        event_type=event_type,
        priority=resolved_priority,
        session_id=session_id,
        turn_id=turn_id,
        task_id=task_id,
        source=source,
        target=target,
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=dedupe_key,
        payload=payload,
    )


__all__ = ["BusEnvelope", "build_envelope"]
