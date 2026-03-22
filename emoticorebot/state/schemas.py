"""State and memory schemas for the Front -> Scheduler -> Core architecture."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class WorldModel(BaseModel):
    focus: str = ""
    mode: Literal["chat", "acting", "waiting"] = "chat"
    recent_intent: str = ""
    last_tool_result: str = ""
    open_threads: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)


class WorldModelUpdate(BaseModel):
    focus: str | None = None
    mode: Literal["chat", "acting", "waiting"] | None = None
    recent_intent: str | None = None
    last_tool_result: str | None = None
    open_threads: list[str] | None = None


class MemoryCandidate(BaseModel):
    memory_id: str = ""
    memory_type: Literal["relationship", "fact", "working", "execution", "reflection"] = "fact"
    summary: str = ""
    detail: str = ""
    confidence: float = 0.0
    stability: float = 0.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LongTermRecord(BaseModel):
    record_id: str = ""
    user_id: str = ""
    session_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    summary: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class CognitiveEvent(BaseModel):
    event_id: str = ""
    user_id: str = ""
    session_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    summary: str = ""
    outcome: str = "unknown"
    reason: str = ""
    needs_deep_reflection: bool = False
    user_text: str = ""
    assistant_text: str = ""
    source_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class MemoryPatch(BaseModel):
    cognitive_append: list[CognitiveEvent] = Field(default_factory=list)
    long_term_append: list[LongTermRecord] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)


class MemoryView(BaseModel):
    raw_layer: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    cognitive_layer: list[dict[str, Any]] = Field(default_factory=list)
    long_term_layer: dict[str, Any] = Field(default_factory=dict)
    projections: dict[str, str] = Field(default_factory=dict)
    current_state: str = ""


class UserEvent(BaseModel):
    event_id: str
    thread_id: str
    session_id: str
    user_id: str
    user_text: str
    created_at: str = Field(default_factory=now_iso)

