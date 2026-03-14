"""Reflection orchestration separated from memory IO."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.agent.reflection.deep import DeepReflectionResult, DeepReflectionService
from emoticorebot.agent.reflection.input import build_reflection_input
from emoticorebot.agent.reflection.memory import MemoryService
from emoticorebot.agent.reflection.turn import TurnReflectionService
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.types import ExecutionInfo, ReflectionInput, TurnReflectionOutput


@dataclass(frozen=True)
class TurnReflectionWriteResult:
    """逐轮反思写入结果"""
    turn_reflection: TurnReflectionOutput = field(default_factory=dict)  # 使用类型定义
    event_count: int = 0
    memory_ids: list[str] = field(default_factory=list)
    memory_count: int = 0
    updated_user: bool = False
    updated_soul: bool = False
    updated_state: bool = False
    state_snapshot: dict[str, Any] | None = None
    should_run_deep_reflection: bool = False
    deep_reflection_reason: str = ""


class ReflectionCoordinator:
    """Owns turn/deep reflection flow and anchor updates."""

    _TURN_SECTION_TITLE = "## 逐轮快写（自动维护）"
    _TURN_USER_MARKER_START = "<!-- TURN_REFLECTION_USER_START -->"
    _TURN_USER_MARKER_END = "<!-- TURN_REFLECTION_USER_END -->"
    _TURN_SOUL_MARKER_START = "<!-- TURN_REFLECTION_SOUL_START -->"
    _TURN_SOUL_MARKER_END = "<!-- TURN_REFLECTION_SOUL_END -->"
    _TURN_STATE_THRESHOLD = 0.65
    _TURN_ANCHOR_MAX_ENTRIES = 10

    def __init__(
        self,
        workspace: Path,
        emotion_manager: EmotionStateManager,
        memory_service: MemoryService,
        *,
        reflection_llm: Any = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ):
        self.workspace = workspace
        self.emotion_mgr = emotion_manager
        self.memory_service = memory_service
        self.turn_reflection = TurnReflectionService(emotion_manager, reflection_llm)
        self.deep_reflection = DeepReflectionService(
            workspace,
            reflection_llm,
            memory_config=memory_config,
            providers_config=providers_config,
        )

    async def write_turn_reflection(self, reflection_input: ReflectionInput) -> TurnReflectionWriteResult:
        input_payload = build_reflection_input(reflection_input)
        output = str(input_payload.get("assistant_output", "") or input_payload.get("output", "") or "").strip()
        user_input = str(input_payload.get("user_input", "") or "").strip()
        if not output:
            return TurnReflectionWriteResult()

        previous_snapshot = self.emotion_mgr.snapshot()
        self.emotion_mgr.update_from_conversation(user_input, output)
        snapshot = self.emotion_mgr.snapshot()
        input_payload["emotion"] = snapshot
        importance = CognitiveEvent.estimate_importance(user_input, output)
        execution = input_payload.get("execution") if isinstance(input_payload.get("execution"), dict) else None

        reflection = await self.turn_reflection.reflect_turn(
            user_input=user_input,
            output=output,
            emotion=snapshot,
            execution=execution,
            source_type=str(input_payload.get("source_type", "user_turn") or "user_turn"),
        )
        turn_reflection = dict(reflection.turn_reflection)

        updated_user, updated_soul, updated_state, state_snapshot = self._apply_turn_reflection_direct_updates(
            turn_reflection
        )

        events = CognitiveEvent.build_turn_events(
            reflection_input=input_payload,
            importance=importance,
            turn_reflection=turn_reflection,
        )
        for event in events:
            CognitiveEvent.append(self.workspace, event)
        event_ids = [event.id for event in events]

        memory_ids = self.memory_service.append_many(
            self._prepare_turn_memory_candidates(
                reflection_input=input_payload,
                turn_reflection=turn_reflection,
                event_ids=event_ids,
            )
        )

        should_run_deep_reflection, deep_reflection_reason = self._default_deep_reflection_decision(
            reflection_input=input_payload,
            importance=importance,
            execution=execution,
            turn_reflection=turn_reflection,
        )
        if should_run_deep_reflection:
            logger.debug("Scheduling deep_reflection after turn_reflection: {}", deep_reflection_reason)

        return TurnReflectionWriteResult(
            turn_reflection=turn_reflection,
            event_count=len(events),
            memory_ids=memory_ids,
            memory_count=len(memory_ids),
            updated_user=updated_user,
            updated_soul=updated_soul,
            updated_state=updated_state,
            state_snapshot=state_snapshot,
            should_run_deep_reflection=should_run_deep_reflection,
            deep_reflection_reason=deep_reflection_reason,
        )

    async def run_deep_reflection(
        self,
        *,
        reason: str = "",
        warm_limit: int = 15,
    ) -> DeepReflectionResult:
        recent_events = CognitiveEvent.recent(self.workspace, limit=max(6, warm_limit))
        if not recent_events:
            return DeepReflectionResult()
        if reason:
            logger.debug("Running deep_reflection: {}", reason)
        return await self.deep_reflection.run_cycle(recent_events)

    def _apply_turn_reflection_direct_updates(
        self,
        turn_reflection: dict[str, Any] | None,
    ) -> tuple[bool, bool, bool, dict[str, Any] | None]:
        payload = turn_reflection if isinstance(turn_reflection, dict) else {}
        updated_user = self.deep_reflection.write_managed_reflection_section(
            filename="USER.md",
            updates=payload.get("user_updates"),
            marker_start=self._TURN_USER_MARKER_START,
            marker_end=self._TURN_USER_MARKER_END,
            intro="以下条目沉淀当前轮高置信用户信息，由 `turn_reflection` 自动维护。",
            section_title=self._TURN_SECTION_TITLE,
            max_entries=self._TURN_ANCHOR_MAX_ENTRIES,
        )
        updated_soul = self.deep_reflection.write_managed_reflection_section(
            filename="SOUL.md",
            updates=payload.get("soul_updates"),
            marker_start=self._TURN_SOUL_MARKER_START,
            marker_end=self._TURN_SOUL_MARKER_END,
            intro="以下条目沉淀当前轮高置信主脑风格修正，由 `turn_reflection` 自动维护。",
            section_title=self._TURN_SECTION_TITLE,
            max_entries=self._TURN_ANCHOR_MAX_ENTRIES,
        )
        updated_state, state_snapshot = self._apply_turn_state_update(payload.get("state_update"))
        return updated_user, updated_soul, updated_state, state_snapshot

    def _apply_turn_state_update(self, payload: Any) -> tuple[bool, dict[str, Any] | None]:
        update = payload if isinstance(payload, dict) else {}
        if not bool(update.get("should_apply", False)):
            return False, None
        pad_state = self._normalize_state_value_map(
            update.get("pad_delta"),
            allowed=("pleasure", "arousal", "dominance"),
            minimum=-1.0,
            maximum=1.0,
        )
        drive_state = self._normalize_state_value_map(
            update.get("drives_delta"),
            allowed=("social", "energy"),
            minimum=0.0,
            maximum=100.0,
        )
        snapshot = self.emotion_mgr.apply_reflection_state_update(
            pad_delta=pad_state,
            drive_delta=drive_state,
        )
        return True, snapshot

    @staticmethod
    def _normalize_state_value_map(
        payload: Any,
        *,
        allowed: tuple[str, ...],
        minimum: float,
        maximum: float,
    ) -> dict[str, float]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, float] = {}
        for key in allowed:
            if key not in payload:
                continue
            try:
                value = float(payload.get(key, 0.0) or 0.0)
            except Exception:
                continue
            value = max(minimum, min(maximum, value))
            precision = 3 if key in {"pleasure", "arousal", "dominance"} else 2
            normalized[key] = round(value, precision)
        return normalized

    def _prepare_turn_memory_candidates(
        self,
        *,
        reflection_input: ReflectionInput,
        turn_reflection: dict[str, Any],
        event_ids: list[str],
    ) -> list[dict[str, Any]]:
        candidates = turn_reflection.get("memory_candidates") if isinstance(turn_reflection, dict) else []
        if not isinstance(candidates, list):
            return []

        session_id = str(reflection_input.get("session_id", "") or "")
        message_id = str(reflection_input.get("message_id", "") or "").strip()
        turn_id = f"turn_{message_id}" if message_id else ""
        tool_names = self._extract_tool_names(reflection_input)

        prepared: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            if not summary and not content:
                continue
            prepared.append(
                {
                    **item,
                    "source": {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "event_ids": event_ids,
                        "producer": "brain.turn_reflection",
                        "tool_names": tool_names,
                    },
                    "links": {
                        "related_ids": [],
                        "evidence_ids": event_ids,
                        "entity_ids": [],
                        "skill_ids": [],
                        "supersedes": [],
                        "invalidates": [],
                    },
                }
            )
        return prepared

    @staticmethod
    def _extract_tool_names(reflection_input: ReflectionInput) -> list[str]:
        names: list[str] = []
        for item in list(reflection_input.get("task_trace", []) or []):
            if not isinstance(item, dict):
                continue
            for key in ("tool_name", "tool", "name", "node"):
                value = str(item.get(key, "") or "").strip()
                if value and value not in names:
                    names.append(value)
        return names[:6]

    @staticmethod
    def _default_deep_reflection_decision(
        *,
        reflection_input: ReflectionInput,
        importance: float,
        execution: ExecutionInfo | None,
        turn_reflection: TurnReflectionOutput,
    ) -> tuple[bool, str]:
        """判断是否需要触发深度反思"""
        if not execution:
            execution = {}

        brain = reflection_input.get("brain")
        execution_review = turn_reflection.get("execution_review", {})
        def _brain_get(key: str, default: Any = "") -> Any:
            if isinstance(brain, dict):
                return brain.get(key, default)
            if brain is None:
                return default
            return getattr(brain, key, default)
        
        status = str(execution.get("status", "")).strip().lower()
        missing = list(execution.get("missing", []))
        effectiveness = str(execution_review.get("effectiveness", "none")).strip().lower()
        failure_reason = str(execution_review.get("main_failure_reason", "")).strip()
        user_updates = list(turn_reflection.get("user_updates", []))
        soul_updates = list(turn_reflection.get("soul_updates", []))
        memory_candidates = list(turn_reflection.get("memory_candidates", []))
        task_reason = str(_brain_get("task_reason", "") or "").strip()

        if execution.get("invoked") and status in {"failed", "need_more"}:
            return True, f"task_requires_followup:{status}"
        if execution.get("invoked") and missing:
            return True, "task_blocked_missing_info"
        if execution.get("invoked") and effectiveness in {"low", "medium"} and failure_reason:
            return True, f"task_review:{failure_reason}"
        if importance >= 0.82 and (user_updates or soul_updates):
            return True, "high_importance_identity_updates"
        if importance >= 0.82 and memory_candidates:
            return True, "high_importance_memory_candidates"
        if task_reason in {
            "loop_limit_reached",
            "brain_requested_task_followup",
            "task_waiting_for_user_input",
        }:
            return True, f"brain_signal:{task_reason}"
        return False, ""


__all__ = ["ReflectionCoordinator", "TurnReflectionWriteResult"]
