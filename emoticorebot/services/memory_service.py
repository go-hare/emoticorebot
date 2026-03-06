"""Memory Service - 记忆管理服务

将 Runtime 中的记忆相关方法提取为独立服务类。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.session.manager import SessionManager


class MemoryService:
    """记忆管理服务

    职责：
    - 写入单轮记忆（PAD状态、关系记忆、情绪记忆）
    - 自动生成技能
    - 后台记忆压缩（语义 consolidation）
    """

    def __init__(
        self,
        workspace: Path,
        memory_facade: MemoryFacade,
        emotion_manager: EmotionStateManager,
        session_manager: SessionManager,
        memory_window: int = 100,
        iq_llm: Any = None,
    ):
        self.workspace = workspace
        self.memory = memory_facade
        self.emotion_mgr = emotion_manager
        self.sessions = session_manager
        self.memory_window = memory_window
        self.iq_llm = iq_llm  # 用于语义记忆压缩

    async def write_turn_memory(self, state: dict[str, Any]) -> None:
        """写入单轮记忆

        包含：
        1. PAD 状态更新
        2. 关系记忆写入（重要性启发式）
        3. 情绪记忆写入
        4. 自动技能生成
        5. 语义记忆压缩（异步后台）
        """
        output = state.get("output", "")
        user_input = state.get("user_input", "")
        if not output:
            return

        # 1. PAD 状态更新
        emotion_event = self.emotion_mgr.update_from_conversation(user_input, output)
        label = self.emotion_mgr.get_emotion_label()

        # 2. 关系记忆写入（含强情绪词评 7，否则 5）
        summary = (
            f"用户：{user_input[:120]}{'...' if len(user_input) > 120 else ''}"
            f" → AI：{output[:120]}{'...' if len(output) > 120 else ''}"
        )
        importance = 7 if any(
            w in user_input for w in ["失恋", "难过", "崩溃", "好烦", "开心", "谢谢", "着急", "焦虑"]
        ) else 5
        self.memory.relational.save(summary, emotion=label, importance=importance)
        logger.debug("Relational memory written: emotion={}, importance={}", label, importance)

        # 3. 情绪记忆写入
        if emotion_event:
            self.memory.affective.save(
                description=f"触发词：{emotion_event.trigger}，行为：{emotion_event.behavior}",
                pleasure=self.emotion_mgr.pad.pleasure,
                arousal=self.emotion_mgr.pad.arousal,
                dominance=self.emotion_mgr.pad.dominance,
                importance=0.5,
            )
            logger.debug("Affective memory written: trigger={}", emotion_event.trigger)

        # 4. 自动技能生成（当 IQ 成功执行且工具调用 >= 2 次）
        iq = state.get("iq")
        if iq is not None and getattr(iq, "success", False) and len(getattr(iq, "tool_calls", [])) >= 2:
            self.generate_skill_from_tool_path(state)

        # 5. 语义记忆压缩（异步后台任务）
        session_id = state.get("session_id", "")
        if session_id:
            asyncio.create_task(self.consolidate_background(session_id))

    def generate_skill_from_tool_path(self, state: dict[str, Any]) -> None:
        """自动生成技能（当工具调用路径有价值时）"""
        task = (state.get("user_input", "") or "auto task").strip()
        skill_name = "_".join(re.findall(r"[\u4e00-\u9fa5a-zA-Z]+", task)[:2]).lower() or "auto_skill"
        skills_dir = self.workspace / "skills" / skill_name
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skills_dir / "SKILL.md"
        if skill_file.exists():
            return

        iq = state.get("iq")
        calls = getattr(iq, "tool_calls", []) if iq is not None else []
        lines = [f"# {task}", "", f"> 自动生成 Skill：{task}", "", "## 工具调用路径"]
        for c in calls:
            lines.append(f"- {c.get('tool', 'unknown')}")
        skill_file.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Auto-generated skill: {}", skill_name)

    async def consolidate_background(self, session_id: str) -> None:
        """异步后台任务：语义记忆压缩

        只有当消息数超过 memory_window 时才压缩。
        需要 iq_llm 已注入（在 __init__ 中通过 iq_llm 参数传入）。
        """
        if self.iq_llm is None:
            logger.debug("Memory consolidation skipped: iq_llm not injected")
            return
        try:
            session = self.sessions.get(session_id)
            if not session:
                return

            if len(session.messages) < self.memory_window:
                return

            from emoticorebot.memory.memory_store import MemoryStore

            store = MemoryStore(self.workspace)
            success = await store.consolidate(
                session,
                llm=self.iq_llm,
                archive_all=False,
                memory_window=self.memory_window,
                cold_store=self.memory.semantic,
            )

            if success:
                self.sessions.save(session)
                logger.info(
                    "✅ Memory consolidated for session: {}",
                    session_id,
                )

        except Exception as e:
            logger.error("Memory consolidation failed for session {}: {}", session_id, e)


__all__ = ["MemoryService"]
