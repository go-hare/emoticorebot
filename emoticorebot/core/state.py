"""Fusion state definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# IQ / EQ 子状态（dataclass，支持属性访问与就地修改）
# ---------------------------------------------------------------------------

@dataclass
class IQState:
    """IQ 参谋层的运行时状态。"""
    task: str = ""
    status: str = "idle"
    analysis: str = ""
    evidence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    recommended_action: str = ""
    selected_experts: list[str] = field(default_factory=list)
    expert_packets: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    rationale_summary: str = ""
    missing_params: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    error: str = ""
    iterations: int = 0


@dataclass
class EQState:
    """EQ 主导层的运行时状态。"""
    emotion: str = "平静"
    pad: dict[str, float] = field(
        default_factory=lambda: {"pleasure": 0.0, "arousal": 0.5, "dominance": 0.5}
    )
    intent: str = ""
    emotional_goal: str = ""
    working_hypothesis: str = ""
    question_to_iq: str = ""
    selected_experts: list[str] = field(default_factory=list)
    expert_questions: dict[str, str] = field(default_factory=dict)
    accepted_experts: list[str] = field(default_factory=list)
    rejected_experts: list[str] = field(default_factory=list)
    arbitration_summary: str = ""
    final_decision: str = ""
    final_message: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# 顶层 Graph 状态
# ---------------------------------------------------------------------------

class FusionState(TypedDict, total=False):
    """Runtime state for fusion graph."""

    user_input: str
    history: list[dict]
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
    history: list[dict] | None = None,
    channel: str = "",
    chat_id: str = "",
    session_id: str = "",
) -> FusionState:
    """Build initial graph state."""
    pad = load_pad_from_workspace(workspace)
    return {
        "user_input": user_input,
        "history": history or [],
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
    "IQState",
    "EQState",
    "create_initial_state",
    "load_pad_from_workspace",
    "get_emotion_label",
]
