"""Formal session world-state models for the main_brain/execution runtime."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from emoticorebot.protocol.task_models import ProtocolModel

ConversationPhase = Literal[
    "idle",
    "chat",
    "multitask_chat",
    "support",
    "task_focus",
    "waiting_user",
    "crisis_response",
]
TaskStatus = Literal["pending", "running", "waiting_user", "scheduled", "done", "failed", "cancelled"]
TaskVisibility = Literal["silent", "concise", "verbose"]
TaskInterruptibility = Literal["never", "important_only", "always"]
TaskKind = Literal["chat", "diagnosis", "reminder", "search", "analysis", "execution", "followup", "other"]
UserEmotion = Literal["neutral", "tired", "annoyed", "sad", "anxious", "happy", "excited", "despair"]
UserEnergy = Literal["low", "medium", "high"]
ChunkStatus = Literal["pending", "running", "done", "failed", "blocked"]
DeliveryMode = Literal["inline", "push", "stream"]


class UserStateSnapshot(ProtocolModel):
    emotion: UserEmotion = "neutral"
    energy: UserEnergy = "medium"
    confidence: float = 0.0


class TaskChunkState(ProtocolModel):
    chunk_id: str
    title: str = ""
    status: ChunkStatus = "pending"


class PerceptionItemSummary(ProtocolModel):
    name: str = ""
    kind: str = ""
    status: str = ""
    summary: str = ""


class PerceptionSummary(ProtocolModel):
    images: list[PerceptionItemSummary] = Field(default_factory=list)
    audio: list[PerceptionItemSummary] = Field(default_factory=list)
    video: list[PerceptionItemSummary] = Field(default_factory=list)
    files: list[PerceptionItemSummary] = Field(default_factory=list)


class SessionTaskState(ProtocolModel):
    task_id: str
    title: str
    kind: TaskKind = "other"
    parent_task_id: str | None = None
    status: TaskStatus = "pending"
    priority: int = 50
    visibility: TaskVisibility = "concise"
    interruptibility: TaskInterruptibility = "important_only"
    user_visible: bool = True
    goal: str = ""
    current_chunk: TaskChunkState | None = None
    recent_observations: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    last_user_visible_update: str = ""
    waiting_for_user: bool = False
    risk_flags: list[str] = Field(default_factory=list)


class ReplyStrategyState(ProtocolModel):
    goal: str = ""
    style: str = ""
    delivery_mode: DeliveryMode = "inline"
    needs_tool: bool = False


class SessionWorldState(ProtocolModel):
    session_id: str
    conversation_phase: ConversationPhase = "idle"
    foreground_task_id: str | None = None
    background_task_ids: list[str] = Field(default_factory=list)
    user_state: UserStateSnapshot = Field(default_factory=UserStateSnapshot)
    active_topics: list[str] = Field(default_factory=list)
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    tasks: dict[str, SessionTaskState] = Field(default_factory=dict)
    perception_summary: PerceptionSummary = Field(default_factory=PerceptionSummary)
    reply_strategy: ReplyStrategyState = Field(default_factory=ReplyStrategyState)
    risk_flags: list[str] = Field(default_factory=list)


class StructuredProgressUpdate(ProtocolModel):
    task_id: str
    stage: str = ""
    status: TaskStatus = "running"
    summary: str = ""
    observations: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    needs_user_input: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionTraceRecord(ProtocolModel):
    trace_id: str
    task_id: str
    kind: str
    message: str
    ts: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ChunkStatus",
    "ConversationPhase",
    "DeliveryMode",
    "PerceptionItemSummary",
    "PerceptionSummary",
    "ReplyStrategyState",
    "SessionTaskState",
    "SessionTraceRecord",
    "SessionWorldState",
    "StructuredProgressUpdate",
    "TaskChunkState",
    "TaskInterruptibility",
    "TaskKind",
    "TaskStatus",
    "TaskVisibility",
    "UserEmotion",
    "UserEnergy",
    "UserStateSnapshot",
]
