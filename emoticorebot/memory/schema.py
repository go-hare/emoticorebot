from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MemoryRecord:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryEvent(MemoryRecord):
    id: str
    timestamp: str
    session_id: str
    channel: str
    actor: str
    kind: str
    content: str
    summary: str
    importance: float = 0.5
    confidence: float = 1.0
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_event_ids: list[str] = field(default_factory=list)


@dataclass
class EpisodicMemory(MemoryRecord):
    id: str
    timestamp: str
    session_id: str
    summary: str
    participants: list[str] = field(default_factory=list)
    topic_tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.7
    emotion_snapshot: dict[str, Any] = field(default_factory=dict)
    source_event_ids: list[str] = field(default_factory=list)


@dataclass
class ReflectiveMemory(MemoryRecord):
    id: str
    created_at: str
    insight: str
    theme: str
    confidence: float = 0.6
    importance: float = 0.5
    evidence_event_ids: list[str] = field(default_factory=list)
    derived_from_memory_ids: list[str] = field(default_factory=list)
    expires_at: str | None = None


@dataclass
class PlanMemory(MemoryRecord):
    id: str
    created_at: str
    updated_at: str
    title: str
    status: str
    kind: str
    owner: str
    related_subjects: list[str] = field(default_factory=list)
    due_at: str | None = None
    next_action: str | None = None
    blockers: list[str] = field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.7
    source_event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "MemoryEvent",
    "EpisodicMemory",
    "ReflectiveMemory",
    "PlanMemory",
]
