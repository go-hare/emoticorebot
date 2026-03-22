"""Schemas for state, memory, and execution coordination."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class Artifact(BaseModel):
    type: Literal["file", "doc", "link", "report", "note"] = "note"
    name: str = ""
    value: str = ""


class CheckState(BaseModel):
    check_id: str
    task_id: str
    goal: str = ""
    instructions: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "done", "failed"] = "pending"
    summary: str = ""
    error: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)


class TaskState(BaseModel):
    task_id: str
    title: str = ""
    goal: str = ""
    status: Literal["running", "done", "failed"] = "running"
    plan: list[str] = Field(default_factory=list)
    current_step: str = ""
    checks: dict[str, CheckState] = Field(default_factory=dict)
    updated_at: str = Field(default_factory=now_iso)


class RunningJob(BaseModel):
    job_id: str
    task_id: str
    check_id: str
    thread_id: str = ""
    goal: str = ""
    workspace: str = ""
    status: Literal["running"] = "running"
    started_at: str = Field(default_factory=now_iso)


class WorldState(BaseModel):
    focus_task_id: str = ""
    tasks: dict[str, TaskState] = Field(default_factory=dict)
    running_jobs: dict[str, RunningJob] = Field(default_factory=dict)


class StatePatch(BaseModel):
    focus_task_id: str = ""
    upsert_tasks: list[TaskState] = Field(default_factory=list)
    remove_task_ids: list[str] = Field(default_factory=list)
    upsert_checks: list[CheckState] = Field(default_factory=list)
    remove_check_ids: list[str] = Field(default_factory=list)
    upsert_running_jobs: list[RunningJob] = Field(default_factory=list)
    remove_job_ids: list[str] = Field(default_factory=list)


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
    task_id: str = ""
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
    task_id: str = ""
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


class ReflectionRequest(BaseModel):
    thread_id: str
    session_id: str
    user_id: str
    reason: str
    trigger: dict[str, Any]
    memory: MemoryView
    world_state: WorldState


class ReflectionSuggestion(BaseModel):
    state_patch: StatePatch = Field(default_factory=StatePatch)
    memory_patch: MemoryPatch = Field(default_factory=MemoryPatch)
