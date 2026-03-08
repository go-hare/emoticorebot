"""Memory Service - 记忆管理服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.models.emotion_state import EmotionStateManager
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

    async def write_turn_memory(self, state: dict[str, Any]) -> None:
        """写入单轮原始认知事件流。"""
        output = state.get("output", "")
        user_input = state.get("user_input", "")
        if not output:
            return

        emotion_event = self.emotion_mgr.update_from_conversation(user_input, output)
        label = self.emotion_mgr.get_emotion_label()
        importance_score = CognitiveEvent.estimate_importance(user_input, output)

        events = CognitiveEvent.build_turn_events(
            state=state,
            emotion_label=label,
            emotion_event=emotion_event,
            pad={
                "pleasure": self.emotion_mgr.pad.pleasure,
                "arousal": self.emotion_mgr.pad.arousal,
                "dominance": self.emotion_mgr.pad.dominance,
            },
            importance=importance_score,
        )
        for event in events:
            CognitiveEvent.append(self.workspace, event)


__all__ = ["MemoryService"]
