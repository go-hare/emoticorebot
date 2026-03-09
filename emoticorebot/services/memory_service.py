"""Memory Service - 记忆管理服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.services.light_reflection import LightReflectionService
from emoticorebot.session.manager import SessionManager


class MemoryService:
    """记忆管理服务：当前只写入认知事件流。"""

    def __init__(
        self,
        workspace: Path,
        emotion_manager: EmotionStateManager,
        session_manager: SessionManager,
        memory_window: int = 100,
        iq_llm: Any = None,
    ):
        self.workspace = workspace
        self.emotion_mgr = emotion_manager
        self.sessions = session_manager
        self.memory_window = memory_window
        self.iq_llm = iq_llm
        self.light_reflection = LightReflectionService(workspace, emotion_manager, iq_llm)

    async def write_turn_memory(self, state: dict[str, Any]) -> None:
        """写入单轮原始认知事件流。"""
        output = state.get("output", "")
        user_input = state.get("user_input", "")
        if not output:
            return

        emotion_event = self.emotion_mgr.update_from_conversation(user_input, output)
        label = self.emotion_mgr.get_emotion_label()
        importance_score = CognitiveEvent.estimate_importance(user_input, output)
        initial_snapshot = self.emotion_mgr.snapshot()
        reflection = await self.light_reflection.reflect_turn(
            user_input=user_input,
            output=output,
            emotion_label=label,
            pad=dict(initial_snapshot.get("pad", {}) or {}),
            drives=dict(initial_snapshot.get("drives", {}) or {}),
        )
        current_snapshot = reflection.state_snapshot or self.emotion_mgr.snapshot()
        current_emotion_label = str(current_snapshot.get("emotion_label", label) or label)

        events = CognitiveEvent.build_turn_events(
            state=state,
            emotion_label=current_emotion_label,
            emotion_event=emotion_event,
            pad={
                "pleasure": float((current_snapshot.get("pad") or {}).get("pleasure", self.emotion_mgr.pad.pleasure)),
                "arousal": float((current_snapshot.get("pad") or {}).get("arousal", self.emotion_mgr.pad.arousal)),
                "dominance": float((current_snapshot.get("pad") or {}).get("dominance", self.emotion_mgr.pad.dominance)),
            },
            drives={
                "social": float((current_snapshot.get("drives") or {}).get("social", self.emotion_mgr.drive.social)),
                "energy": float((current_snapshot.get("drives") or {}).get("energy", self.emotion_mgr.drive.energy)),
            },
            importance=importance_score,
            light_insight=reflection.light_insight,
        )
        for event in events:
            CognitiveEvent.append(self.workspace, event)


__all__ = ["MemoryService"]
