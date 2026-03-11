"""Small shared brain-layer types."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

BrainFinalDecision = Literal["", "answer", "ask_user", "continue"]
BrainTaskAction = Literal["", "none", "create_task", "fill_task"]


class BrainControlPacket(TypedDict, total=False):
    action: BrainTaskAction
    reason: str
    final_decision: BrainFinalDecision
    message: str
    task_brief: str
    task: dict[str, Any]
    intent: str
    working_hypothesis: str
    notify_user: bool
    retrieval_query: str
    retrieval_focus: list[str]
    retrieved_memory_ids: list[str]
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


__all__ = [
    "BrainControlPacket",
    "BrainFinalDecision",
    "BrainTaskAction",
]
