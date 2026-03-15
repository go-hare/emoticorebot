"""Safety payload models for the v3 runtime protocol."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .task_models import ProtocolModel

SafetyDecision = Literal["allowed", "redacted", "blocked", "warning"]


class SafetyAuditPayload(ProtocolModel):
    decision_id: str
    decision: SafetyDecision
    intercepted_event_type: str
    policy_name: str | None = None
    reason: str | None = None
    match_spans: list[str] = Field(default_factory=list)
    redaction_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["SafetyAuditPayload", "SafetyDecision"]
