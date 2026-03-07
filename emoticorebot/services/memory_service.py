"""Memory Service - 记忆管理服务

将 Runtime 中的记忆相关方法提取为独立服务类。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.memory.extractor import MemoryExtractor
from emoticorebot.memory.memory_facade import MemoryFacade
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.session.manager import SessionManager
from emoticorebot.utils.llm_utils import extract_message_text


class MemoryService:
    """记忆管理服务

    职责：
    - 写入单轮记忆（PAD状态、关系记忆、情绪记忆）
    - 自动生成技能
    - 后台记忆压缩（结构化 semantic consolidation）
    """

    _SEMANTIC_CONSOLIDATION_PROMPT = """你是长期记忆抽取器。请从下面这段对话中提炼适合长期保留的结构化事实。

抽取原则：
1. 只保留较稳定或后续有用的信息：用户偏好、习惯、项目背景、约定、计划、反复出现的问题、任务上下文。
2. 不要机械复述整段对话，不要输出瞬时寒暄。
3. 每条 fact 必须是可单独检索的一句话。
4. 若没有值得保存的长期信息，返回空数组。

当前已有语义记忆（用于避免重复）：
{current_semantic}

待处理对话：
{conversation}

仅输出 JSON：
{{
  "facts": [
    {{
      "text": "...",
      "importance": 1-10,
      "category": "preference|project|habit|event|other",
      "confidence": 0.0,
      "subject": "user|assistant|project|...",
      "predicate": "likes|is_working_on|needs|plans|...",
      "object": "..."
    }}
  ]
}}
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
        self.extractor = MemoryExtractor(workspace)

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
        importance_score = self.extractor.estimate_importance(user_input, output)

        # 1.5 Event Stream / Episodic / Plan 写入（新主路径）
        events = self.extractor.build_turn_events(
            state=state,
            emotion_label=label,
            importance=importance_score,
        )
        for event in events:
            self.memory.events.save(event)
        source_event_ids = [event.id for event in events]

        emotion_trace = self.extractor.build_emotion_event(
            session_id=str(state.get("session_id", "")),
            channel=str(state.get("channel", "")),
            emotion_event=emotion_event,
            pad={
                "pleasure": self.emotion_mgr.pad.pleasure,
                "arousal": self.emotion_mgr.pad.arousal,
                "dominance": self.emotion_mgr.pad.dominance,
            },
            source_event_ids=source_event_ids,
        )
        if emotion_trace is not None:
            self.memory.events.save(emotion_trace)
            source_event_ids.append(emotion_trace.id)

        episode = self.extractor.build_episode(
            state=state,
            source_event_ids=source_event_ids,
            importance=importance_score,
            emotion_label=label,
        )
        if episode is not None:
            self.memory.episodic.save(episode)

        arbitration_reflection = self.extractor.build_arbitration_reflection(
            state=state,
            source_event_ids=source_event_ids,
            importance=importance_score,
        )
        if arbitration_reflection is not None:
            self.memory.reflective.save(arbitration_reflection)

        plan = self.extractor.build_plan_memory(state=state, source_event_ids=source_event_ids)
        if plan is not None:
            self.memory.plans.save(plan)

        # 2. 关系记忆写入（含强情绪词评 7，否则 5）
        summary = (
            f"用户：{user_input[:120]}{'...' if len(user_input) > 120 else ''}"
            f" → AI：{output[:120]}{'...' if len(output) > 120 else ''}"
        )
        importance = 7 if any(
            w in user_input for w in ["失恋", "难过", "崩溃", "好烦", "开心", "谢谢", "着急", "焦虑"]
        ) else 5
        self.memory.relational.save(
            summary,
            emotion=label,
            importance=importance,
            confidence=0.78,
            source_event_ids=source_event_ids,
            relation_type="interaction",
            target="user",
        )
        logger.debug("Relational memory written: emotion={}, importance={}", label, importance)

        # 3. 情绪记忆写入
        if emotion_event:
            self.memory.affective.save(
                description=f"触发词：{emotion_event.trigger}，行为：{emotion_event.behavior}",
                pleasure=self.emotion_mgr.pad.pleasure,
                arousal=self.emotion_mgr.pad.arousal,
                dominance=self.emotion_mgr.pad.dominance,
                importance=0.5,
                confidence=0.8,
                source_event_ids=source_event_ids,
            )
            logger.debug("Affective memory written: trigger={}", emotion_event.trigger)

        # 4. 自动技能生成（当 IQ 成功执行且工具调用 >= 2 次）
        iq = state.get("iq")
        if iq is not None and getattr(iq, "status", "") == "completed" and len(getattr(iq, "tool_calls", [])) >= 2:
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

            keep_count = max(4, self.memory_window // 2)
            if len(session.messages) <= keep_count:
                return

            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return

            success = await self._consolidate_semantic_memories(
                session=session,
                old_messages=old_messages,
                keep_count=keep_count,
            )

            if success:
                self.sessions.save(session)
                logger.info(
                    "✅ Memory consolidated for session: {}",
                    session_id,
                )

        except Exception as e:
            logger.error("Memory consolidation failed for session {}: {}", session_id, e)

    async def _consolidate_semantic_memories(
        self,
        *,
        session,
        old_messages: list[dict[str, Any]],
        keep_count: int,
    ) -> bool:
        lines = self._format_conversation_slice(old_messages)
        if not lines:
            session.last_consolidated = max(0, len(session.messages) - keep_count)
            return True

        prompt = self._SEMANTIC_CONSOLIDATION_PROMPT.format(
            current_semantic=self.memory.semantic.get_context(query="", k=12) or "(empty)",
            conversation="\n".join(lines),
        )
        response = await self.iq_llm.ainvoke([{"role": "user", "content": prompt}])
        payload = self._extract_json_payload(extract_message_text(response)) or {}

        written = 0
        existing_texts = {
            str(item.get("text", "")).strip().lower()
            for item in self.memory.semantic.read_all()
            if isinstance(item, dict)
        }

        for fact in payload.get("facts", []):
            if not isinstance(fact, dict):
                continue
            text = self._compact_text(str(fact.get("text", "") or ""), limit=240)
            normalized = text.lower().strip()
            if not normalized or normalized in existing_texts:
                continue

            self.memory.semantic.save(
                text=text,
                tags=self.extractor._extract_tags(text),
                importance=int(fact.get("importance", 5) or 5),
                category=str(fact.get("category", "other") or "other"),
                confidence=float(fact.get("confidence", 0.7) or 0.7),
                subject=str(fact.get("subject", "") or ""),
                predicate=str(fact.get("predicate", "") or ""),
                object_value=str(fact.get("object", "") or ""),
            )
            existing_texts.add(normalized)
            written += 1

        session.last_consolidated = max(0, len(session.messages) - keep_count)
        logger.info("Semantic consolidation wrote {} facts for {}", written, session.key)
        return True

    @staticmethod
    def _format_conversation_slice(messages: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for message in messages:
            content = str(message.get("content", "") or "").strip()
            if not content:
                continue
            role = str(message.get("role", "unknown") or "unknown").upper()
            timestamp = str(message.get("timestamp", "?") or "?")[:16]
            tools = message.get("tools_used") or []
            tools_hint = f" [tools: {', '.join(str(tool) for tool in tools)}]" if tools else ""
            lines.append(f"[{timestamp}] {role}{tools_hint}: {content}")
        return lines

    @staticmethod
    def _extract_json_payload(text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            payload = json.loads(match.group())
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    @staticmethod
    def _compact_text(text: str, limit: int = 240) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"


__all__ = ["MemoryService"]
