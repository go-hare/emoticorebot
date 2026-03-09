"""Orchestration state definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

ExecutorStatus = Literal["idle", "queued", "running", "completed", "needs_input", "uncertain", "failed"]
ExecutorRecommendedAction = Literal["", "answer", "ask_user", "continue_deliberation"]
MainBrainFinalDecision = Literal["", "answer", "ask_user", "continue_deliberation"]


class ExecutorResultPacket(TypedDict):
    """Normalized executor result packet returned into the orchestration graph."""

    status: Literal["completed", "needs_input", "uncertain", "failed"]
    analysis: str
    risks: list[str]
    missing: list[str]
    recommended_action: Literal["answer", "ask_user", "continue_deliberation"]
    confidence: float


class MainBrainDeliberationPacket(TypedDict):
    """Main-brain first-pass packet before deciding whether to use the executor."""

    intent: str
    working_hypothesis: str
    need_executor: bool
    question_to_executor: str
    final_message: str


class MainBrainFinalizePacket(TypedDict):
    """Main-brain final decision after reading the executor packet."""

    decision: Literal["answer", "ask_user", "continue_deliberation"]
    message: str
    question_to_executor: str


@dataclass
class ExecutorState:
    """Runtime state for the subordinate execution layer."""

    request: str = ""
    status: ExecutorStatus = "idle"
    analysis: str = ""
    risks: list[str] = field(default_factory=list)
    recommended_action: ExecutorRecommendedAction = ""
    confidence: float = 0.0
    missing_params: list[str] = field(default_factory=list)
    attempts: int = 0
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class MainBrainState:
    """Runtime state for the main-brain layer."""

    emotion: str = "平静"
    pad: dict[str, float] = field(
        default_factory=lambda: {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    )
    intent: str = ""
    working_hypothesis: str = ""
    question_to_executor: str = ""
    final_decision: MainBrainFinalDecision = ""
    final_message: str = ""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OrchestrationState(TypedDict, total=False):
    """Runtime state for the orchestration graph."""

    user_input: str
    dialogue_history: list[dict]
    internal_history: list[dict]
    executor_trace: list[dict]
    executor: ExecutorState
    main_brain: MainBrainState
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


def create_initial_state(
    user_input: str,
    workspace: Path,
    dialogue_history: list[dict] | None = None,
    internal_history: list[dict] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> OrchestrationState:
    """Build the initial orchestration state."""
    pad = load_pad_from_workspace(workspace)
    return {
        "user_input": user_input,
        "dialogue_history": dialogue_history or [],
        "internal_history": internal_history or [],
        "executor": ExecutorState(),
        "main_brain": MainBrainState(
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
    "ExecutorRecommendedAction",
    "ExecutorResultPacket",
    "ExecutorState",
    "ExecutorStatus",
    "MainBrainDeliberationPacket",
    "MainBrainFinalizePacket",
    "MainBrainState",
    "OrchestrationState",
    "create_initial_state",
    "get_emotion_label",
    "load_pad_from_workspace",
]
