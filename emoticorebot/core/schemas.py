"""Schemas for core decisions and reflection results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from emoticorebot.state.schemas import MemoryPatch, StatePatch


class SpeakIntent(BaseModel):
    mode: Literal["none", "reply", "followup"] = "none"
    text: str = ""
    priority: Literal["low", "normal", "high"] = "normal"


class DispatchCheck(BaseModel):
    job_id: str = ""
    task_id: str = ""
    check_id: str = ""
    thread_id: str = ""
    goal: str = ""
    instructions: list[str] = Field(default_factory=list)
    workspace: str = ""


class MainDecision(BaseModel):
    state_patch: StatePatch = Field(default_factory=StatePatch)
    memory_patch: MemoryPatch = Field(default_factory=MemoryPatch)
    dispatch_checks: list[DispatchCheck] = Field(default_factory=list)
    speak_intent: SpeakIntent = Field(default_factory=SpeakIntent)
    run_reflection: bool = False
    reflection_reason: str = ""


class ReflectionResult(BaseModel):
    memory_patch: MemoryPatch = Field(default_factory=MemoryPatch)
    world_state_suggestion: StatePatch = Field(default_factory=StatePatch)
    mode: Literal["light", "deep", "crystallize", "stop"] = "stop"
