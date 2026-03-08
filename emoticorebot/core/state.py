"""Fusion state definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

IQStatus = Literal["idle", "queued", "running", "completed", "needs_input", "uncertain", "failed"]
IQRecommendedAction = Literal["", "answer", "ask_user", "continue_deliberation"]
EQFinalDecision = Literal["", "answer", "ask_user", "continue_deliberation"]


class IQResultPacket(TypedDict):
    """Normalized IQ result packet passed from IQService back into the graph."""

    status: Literal["completed", "needs_input", "uncertain", "failed"]
    analysis: str
    risks: list[str]
    missing: list[str]
    recommended_action: Literal["answer", "ask_user", "continue_deliberation"]
    confidence: float


class EQDeliberationPacket(TypedDict):
    """Normalized EQ first-pass packet before deciding whether to consult IQ."""

    intent: str
    working_hypothesis: str
    need_iq: bool
    question_to_iq: str
    final_message: str


class EQFinalizePacket(TypedDict):
    """Normalized EQ final decision after reading the IQ packet."""

    decision: Literal["answer", "ask_user", "continue_deliberation"]
    message: str
    question_to_iq: str

# ---------------------------------------------------------------------------
# IQ / EQ 子状态（dataclass，支持属性访问与就地修改）
# ---------------------------------------------------------------------------

@dataclass
class IQState:
    """IQ 参谋层的运行时状态。"""
    request: str = ""
    status: IQStatus = "idle"
    analysis: str = ""
    risks: list[str] = field(default_factory=list)
    recommended_action: IQRecommendedAction = ""
    confidence: float = 0.0
    missing_params: list[str] = field(default_factory=list)
    attempts: int = 0
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class EQState:
    """EQ 主导层的运行时状态。"""
    emotion: str = "平静"
    pad: dict[str, float] = field(
        default_factory=lambda: {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    )
    intent: str = ""
    working_hypothesis: str = ""
    question_to_iq: str = ""
    final_decision: EQFinalDecision = ""
    final_message: str = ""
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# ---------------------------------------------------------------------------
# 顶层 Graph 状态
# ---------------------------------------------------------------------------

class FusionState(TypedDict, total=False):
    """Runtime state for fusion graph."""

    user_input: str
    # External conversation history for the user↔EQ channel.
    # This is persisted across turns and must never be fed into IQ directly.
    user_eq_history: list[dict]
    # Internal deliberation history for the EQ↔IQ channel.
    # This is single-turn only and exists only to support retries / follow-up IQ rounds.
    eq_iq_history: list[dict]
    # Fine-grained DeepAgents streaming trace for the current IQ run only.
    iq_trace: list[dict]
    iq: IQState
    eq: EQState
    done: bool
    output: str
    workspace: str
    session_id: str
    channel: str
    chat_id: str
    on_progress: Any     # Callable[[str], Awaitable[None]]（进度回调）
    metadata: dict       # 元数据（intent_params 等）
    media: list[str]     # 媒体文件路径列表
    discussion_count: int


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def load_pad_from_workspace(workspace: Path) -> dict[str, float]:
    """Load PAD from workspace/current_state.md."""
    state_file = workspace / "current_state.md"
    pad = {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    if not state_file.exists():
        return pad
    try:
        text = state_file.read_text(encoding="utf-8")
        p = re.search(r"Pleasure[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
        a = re.search(r"Arousal[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
        d = re.search(r"Dominance[^|]*\|\s*([-\d.]+)", text, re.IGNORECASE)
        if p:
            pad["pleasure"] = max(-1.0, min(1.0, float(p.group(1))))
        if a:
            pad["arousal"] = max(-1.0, min(1.0, float(a.group(1))))
        if d:
            pad["dominance"] = max(-1.0, min(1.0, float(d.group(1))))
    except Exception:
        return {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    return pad


def get_emotion_label(pad: dict[str, float]) -> str:
    pleasure = float(pad.get("pleasure", 0.0))
    arousal = float(pad.get("arousal", 0.5))
    if pleasure < -0.5:
        return "悲伤" if arousal < 0.3 else "愤怒"
    if pleasure > 0.5:
        return "兴奋" if arousal > 0.7 else "开心"
    if arousal < 0.2:
        return "困倦"
    return "平静"


def create_initial_state(
    user_input: str,
    workspace: Path,
    user_eq_history: list[dict] | None = None,
    eq_iq_history: list[dict] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> FusionState:
    """Build initial graph state.

    `user_eq_history` is cross-turn external conversation history.
    `eq_iq_history` is a fresh per-turn internal deliberation buffer.
    """
    pad = load_pad_from_workspace(workspace)
    return {
        "user_input": user_input,
        "user_eq_history": user_eq_history or [],
        "eq_iq_history": eq_iq_history or [],
        "iq": IQState(),
        "eq": EQState(
            emotion=get_emotion_label(pad),
            pad=pad,
        ),
        "done": False,
        "output": "",
        "discussion_count": 0,
        "workspace": str(workspace),
        "session_id": session_id,
        "channel": channel,
        "chat_id": chat_id,
    }


__all__ = [
    "FusionState",
    "IQResultPacket",
    "IQRecommendedAction",
    "IQStatus",
    "IQState",
    "EQState",
    "create_initial_state",
    "load_pad_from_workspace",
    "get_emotion_label",
]
