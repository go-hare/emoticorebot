"""Schemas for execution jobs and results."""

from __future__ import annotations

from pydantic import BaseModel, Field

from emoticorebot.state.schemas import Artifact


class JobSpec(BaseModel):
    job_id: str
    task_id: str
    check_id: str
    thread_id: str = ""
    goal: str
    instructions: list[str] = Field(default_factory=list)
    workspace: str


class JobResult(BaseModel):
    job_id: str
    task_id: str
    check_id: str
    thread_id: str = ""
    status: str
    summary: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    error: str = ""
