from __future__ import annotations

import hashlib
import re
from datetime import datetime
from uuid import uuid4

from emoticorebot.memory.schema import EpisodicMemory, MemoryEvent, PlanMemory, ReflectiveMemory


class MemoryExtractor:
    def __init__(self, workspace):
        self.workspace = workspace

    def build_turn_events(
        self,
        *,
        state: dict,
        emotion_label: str,
        importance: float,
    ) -> list[MemoryEvent]:
        timestamp = datetime.now().isoformat()
        session_id = str(state.get("session_id", ""))
        channel = str(state.get("channel", ""))
        user_input = str(state.get("user_input", "") or "").strip()
        output = str(state.get("output", "") or "").strip()
        iq = state.get("iq")
        tags = self._extract_tags(f"{user_input} {output}")
        entities = self._extract_entities(f"{user_input} {output}")

        events: list[MemoryEvent] = []
        if user_input:
            user_event = MemoryEvent(
                id=f"evt_{uuid4().hex}",
                timestamp=timestamp,
                session_id=session_id,
                channel=channel,
                actor="user",
                kind="dialogue",
                content=user_input,
                summary=self._summarize(user_input, 180),
                importance=importance,
                confidence=1.0,
                tags=tags,
                entities=entities,
                metadata={"emotion_label": emotion_label},
            )
            events.append(user_event)

        if output:
            eq = state.get("eq")
            assistant_metadata = {
                "emotion_label": emotion_label,
                "iq_question": getattr(iq, "request", "") if iq is not None else "",
                "iq_confidence": float(getattr(iq, "confidence", 0.0) or 0.0) if iq is not None else 0.0,
                "iq_missing_params": list(getattr(iq, "missing_params", []) or []) if iq is not None else [],
                "eq_decision": str(getattr(eq, "final_decision", "") or "") if eq is not None else "",
            }
            source_ids = [events[0].id] if events else []
            assistant_event = MemoryEvent(
                id=f"evt_{uuid4().hex}",
                timestamp=timestamp,
                session_id=session_id,
                channel=channel,
                actor="assistant",
                kind="dialogue",
                content=output,
                summary=self._summarize(output, 180),
                importance=importance,
                confidence=float(getattr(iq, "confidence", 0.8) or 0.8) if iq is not None else 0.8,
                tags=tags,
                entities=entities,
                metadata=assistant_metadata,
                source_event_ids=source_ids,
            )
            events.append(assistant_event)
        return events

    def build_emotion_event(
        self,
        *,
        session_id: str,
        channel: str,
        emotion_event,
        pad: dict[str, float],
        source_event_ids: list[str],
    ) -> MemoryEvent | None:
        if emotion_event is None:
            return None
        timestamp = datetime.now().isoformat()
        content = f"触发词：{emotion_event.trigger}；行为：{emotion_event.behavior}"
        return MemoryEvent(
            id=f"evt_{uuid4().hex}",
            timestamp=timestamp,
            session_id=session_id,
            channel=channel,
            actor="assistant",
            kind="emotion",
            content=content,
            summary=self._summarize(content, 160),
            importance=0.55,
            confidence=0.8,
            tags=["emotion", emotion_event.trigger],
            entities=[],
            metadata={
                "pleasure": float(pad.get("pleasure", 0.0)),
                "arousal": float(pad.get("arousal", 0.5)),
                "dominance": float(pad.get("dominance", 0.5)),
            },
            source_event_ids=source_event_ids,
        )

    def build_episode(
        self,
        *,
        state: dict,
        source_event_ids: list[str],
        importance: float,
        emotion_label: str,
    ) -> EpisodicMemory | None:
        user_input = str(state.get("user_input", "") or "").strip()
        output = str(state.get("output", "") or "").strip()
        if not user_input and not output:
            return None
        timestamp = datetime.now().isoformat()
        combined = f"用户: {user_input} | 助手: {output}".strip()
        return EpisodicMemory(
            id=f"epi_{uuid4().hex}",
            timestamp=timestamp,
            session_id=str(state.get("session_id", "")),
            summary=self._summarize(combined, 240),
            participants=["user", "assistant"],
            topic_tags=self._extract_tags(f"{user_input} {output}"),
            importance=importance,
            confidence=0.75,
            emotion_snapshot={"label": emotion_label},
            source_event_ids=source_event_ids,
        )

    def build_plan_memory(
        self,
        *,
        state: dict,
        source_event_ids: list[str],
    ) -> PlanMemory | None:
        iq = state.get("iq")
        request = str(getattr(iq, "request", "") or "").strip() if iq is not None else ""
        if not request:
            return None
        timestamp = datetime.now().isoformat()
        plan_id = self._build_plan_id(session_id=str(state.get("session_id", "") or ""), request=request)
        related_subjects = self._extract_tags(request, limit=6)
        if getattr(iq, "status", "") == "needs_input":
            return PlanMemory(
                id=plan_id,
                created_at=timestamp,
                updated_at=timestamp,
                title=request,
                status="blocked",
                kind="followup",
                owner="shared",
                related_subjects=related_subjects,
                next_action="等待用户补充缺失信息",
                blockers=list(getattr(iq, "missing_params", []) or []),
                importance=0.7,
                confidence=0.75,
                source_event_ids=source_event_ids,
                metadata={"reason": getattr(iq, "error", "")},
            )
        if getattr(iq, "status", "") == "completed":
            return PlanMemory(
                id=plan_id,
                created_at=timestamp,
                updated_at=timestamp,
                title=request,
                status="done",
                kind="task",
                owner="assistant",
                related_subjects=related_subjects,
                next_action=None,
                importance=0.5,
                confidence=float(getattr(iq, "confidence", 0.7) or 0.7),
                source_event_ids=source_event_ids,
                metadata={"result": getattr(iq, "analysis", "")},
            )
        return None

    def build_arbitration_reflection(
        self,
        *,
        state: dict,
        source_event_ids: list[str],
        importance: float,
    ) -> ReflectiveMemory | None:
        eq = state.get("eq")
        iq = state.get("iq")
        if eq is None or iq is None:
            return None

        discussion_count = int(state.get("discussion_count", 0) or 0)
        final_decision = str(getattr(eq, "final_decision", "") or "").strip()
        recommended_action = str(getattr(iq, "recommended_action", "") or "").strip()

        should_write = discussion_count > 1 or final_decision == "ask_user"
        if not should_write:
            return None

        question = str(getattr(iq, "request", "") or state.get("user_input", "") or "").strip()
        question_summary = self._summarize(question, 80)
        decision_text = final_decision or recommended_action or "answer"
        insight = (
            f"围绕“{question_summary}”这条内部问题，IQ 进行了理性分析与执行，"
            f"经历 {max(1, discussion_count)} 轮内部往返，最终导向 {decision_text}。"
        )
        memory_importance = max(0.45, min(0.95, importance + (0.05 if discussion_count > 1 else 0.0)))
        return ReflectiveMemory(
            id=f"ref_{uuid4().hex}",
            created_at=datetime.now().isoformat(),
            insight=insight,
            theme="iq_process",
            confidence=max(0.6, min(0.95, float(getattr(iq, "confidence", 0.7) or 0.7))),
            importance=memory_importance,
            evidence_event_ids=list(source_event_ids),
            derived_from_memory_ids=[],
            expires_at=None,
        )

    @staticmethod
    def _build_plan_id(*, session_id: str, request: str) -> str:
        normalized_request = re.sub(r"\s+", " ", request.strip().lower())
        seed = f"{session_id}::{normalized_request}" if session_id else normalized_request
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        return f"plan_{digest}"

    @staticmethod
    def estimate_importance(user_input: str, output: str) -> float:
        text = f"{user_input} {output}"
        score = 0.45
        if any(token in text for token in ["失恋", "难过", "焦虑", "崩溃", "喜欢", "谢谢", "约会"]):
            score += 0.2
        if any(token in text for token in ["明天", "下周", "提醒", "计划", "安排", "待办"]):
            score += 0.15
        if "?" in text or "？" in text:
            score += 0.05
        return max(0.1, min(0.95, score))

    @staticmethod
    def _extract_tags(text: str, limit: int = 8) -> list[str]:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", text)
        unique = list(dict.fromkeys(token.lower() for token in tokens if token.strip()))
        return unique[:limit]

    @staticmethod
    def _extract_entities(text: str, limit: int = 6) -> list[str]:
        entities = re.findall(r"[A-Z][a-zA-Z0-9_-]+|[\u4e00-\u9fff]{2,6}", text)
        unique = list(dict.fromkeys(entity for entity in entities if entity.strip()))
        return unique[:limit]

    @staticmethod
    def _summarize(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"


__all__ = ["MemoryExtractor"]
