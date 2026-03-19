"""Reflection payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .task_models import ProtocolModel

MemoryType = Literal["persona", "user_model", "episodic", "task_experience", "tool_experience"]
MemoryTarget = Literal["persona", "user_model"]


class ReflectionSignalPayload(ProtocolModel):
    trigger_id: str
    reason: str | None = None
    source_event_id: str | None = None
    task_id: str | None = None
    recent_context_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReflectionWriteRequestPayload(ProtocolModel):
    request_id: str
    memory_type: MemoryType
    summary: str | None = None
    content: str | None = None
    confidence: float | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    source_component: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReflectionWriteCommittedPayload(ProtocolModel):
    request_id: str
    memory_id: str
    memory_type: MemoryType
    committed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReflectionUpdatePayload(ProtocolModel):
    update_id: str
    target: MemoryTarget
    summary: str | None = None
    content: str | None = None
    confidence: float | None = None
    source_memory_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "MemoryTarget",
    "MemoryType",
    "ReflectionUpdatePayload",
    "ReflectionWriteCommittedPayload",
    "ReflectionWriteRequestPayload",
    "ReflectionSignalPayload",
]

