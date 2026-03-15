"""Structured task result model aligned with the v3 task protocol."""

from __future__ import annotations

from pydantic import Field

from .task_models import ContentBlock, ProtocolModel


class TaskExecutionResult(ProtocolModel):
    summary: str | None = None
    result_text: str | None = None
    result_blocks: list[ContentBlock] = Field(default_factory=list)
    artifacts: list[ContentBlock] = Field(default_factory=list)
    confidence: float | None = None
    reviewer_required: bool | None = None


__all__ = ["TaskExecutionResult"]
