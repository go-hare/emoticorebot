"""Brain-layer turn state definitions with task-model compatibility exports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from emoticorebot.tasks import (
    CentralPacketStatus,
    CentralResultPacket,
    TaskControlState,
    TaskRecommendedAction,
    TaskState,
    TaskStatus,
)

BrainFinalDecision = Literal["", "answer", "ask_user", "continue"]
BrainTaskAction = Literal[
    "",
    "none",
    "create_task",
    "continue_task",
    "pause_task",
    "resume_task",
    "cancel_task",
    "steer_task",
    "reprioritize_task",
    "request_report",
    "takeover_task",
    "defer",
]


class BrainUnderstandingPacket(TypedDict, total=False):
    """Brain understanding packet before deciding the next turn action."""

    intent: str
    working_hypothesis: str
    turn_path: Literal["answer", "task"]
    path_reason: str
    retrieval_query: str
    retrieval_focus: list[str]
    retrieved_memory_ids: list[str]
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class BrainFinalizePacket(TypedDict, total=False):
    """Brain final decision after reading the task packet."""

    final_decision: Literal["answer", "ask_user", "continue"]
    final_message: str
    decision: Literal["answer", "ask_user", "continue"]
    message: str
    task_brief: str
    retrieval_query: str
    retrieval_focus: list[str]
    retrieved_memory_ids: list[str]
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class BrainControlPacket(TypedDict, total=False):
    """Explicit brain control decision for task orchestration."""

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


@dataclass
class BrainState:
    """Runtime state for the brain layer."""

    emotion: str = "平静"
    pad: dict[str, float] = field(
        default_factory=lambda: {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    )
    intent: str = ""
    working_hypothesis: str = ""
    retrieval_query: str = ""
    retrieval_focus: list[str] = field(default_factory=list)
    retrieved_memory_ids: list[str] = field(default_factory=list)
    task_request: str = ""
    task_brief: str = ""
    final_decision: BrainFinalDecision = ""
    final_message: str = ""
    task_action: BrainTaskAction = ""
    task_reason: str = ""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class TurnState(TypedDict, total=False):
    """Runtime state for the explicit brain -> central loop."""

    user_input: str
    dialogue_history: list[dict]
    internal_history: list[dict]
    task_trace: list[dict]
    task: TaskState
    brain: BrainState
    done: bool
    output: str
    workspace: str
    session_id: str
    channel: str
    chat_id: str
    on_progress: Any
    metadata: dict
    media: list[str]
    loop_count: int


def load_pad_from_workspace(workspace: Path) -> dict[str, float]:
    """Load PAD from workspace/current_state.md."""
    state_file = workspace / "current_state.md"
    pad = {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    if not state_file.exists():
        return pad
    try:
        text = state_file.read_text(encoding="utf-8")
        pleasure = re.search(r"Pleasure[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
        arousal = re.search(r"Arousal[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
        dominance = re.search(r"Dominance[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
        if pleasure:
            pad["pleasure"] = max(-1.0, min(1.0, float(pleasure.group(1))))
        if arousal:
            pad["arousal"] = max(-1.0, min(1.0, float(arousal.group(1))))
        if dominance:
            pad["dominance"] = max(-1.0, min(1.0, float(dominance.group(1))))
    except Exception:
        return {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    return pad


def get_emotion_label(pad: dict[str, float]) -> str:
    pleasure = float(pad.get("pleasure", 0.0))
    arousal = float(pad.get("arousal", 0.5))
    if pleasure < -0.5:
        return "难过" if arousal < 0.3 else "生气"
    if pleasure > 0.5:
        return "兴奋" if arousal > 0.7 else "开心"
    if arousal < 0.2:
        return "低落"
    return "平静"


def create_turn_state(
    user_input: str,
    workspace: Path,
    dialogue_history: list[dict] | None = None,
    internal_history: list[dict] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> TurnState:
    """Build the initial turn state."""
    pad = load_pad_from_workspace(workspace)
    return {
        "user_input": user_input,
        "dialogue_history": dialogue_history or [],
        "internal_history": internal_history or [],
        "task": TaskState(),
        "brain": BrainState(
            emotion=get_emotion_label(pad),
            pad=pad,
        ),
        "done": False,
        "output": "",
        "loop_count": 0,
        "workspace": str(workspace),
        "session_id": session_id,
        "channel": channel,
        "chat_id": chat_id,
    }


__all__ = [
    "TaskControlState",
    "TaskStatus",
    "CentralPacketStatus",
    "TaskRecommendedAction",
    "CentralResultPacket",
    "TaskState",
    "BrainUnderstandingPacket",
    "BrainControlPacket",
    "BrainTaskAction",
    "BrainFinalizePacket",
    "BrainState",
    "TurnState",
    "create_turn_state",
    "get_emotion_label",
    "load_pad_from_workspace",
]
