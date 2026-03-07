"""Base contracts for lightweight IQ experts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ExpertContext:
    task: str
    user_input: str
    history: list[dict[str, Any]]
    intent_params: dict[str, Any] | None = None
    pending_task: dict[str, Any] | None = None
    channel: str = ""
    chat_id: str = ""
    media: list[str] | None = None
    on_progress: Any = None
    action_packet: dict[str, Any] | None = None
    memory_packet: dict[str, Any] | None = None


@dataclass
class ExpertPacket:
    expert: str
    status: str
    answer: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    proposed_action: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expert": self.expert,
            "status": self.status,
            "answer": self.answer,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "risks": list(self.risks),
            "missing": list(self.missing),
            "proposed_action": self.proposed_action,
            "metadata": dict(self.metadata),
        }


class BaseExpert(Protocol):
    name: str

    async def run(self, context: ExpertContext) -> ExpertPacket:
        ...


__all__ = ["BaseExpert", "ExpertContext", "ExpertPacket"]
