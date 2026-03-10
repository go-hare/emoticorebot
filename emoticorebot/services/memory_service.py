"""Memory service aligned with session -> cognitive_event -> reflection -> memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.services.deep_reflection import DeepReflectionResult, DeepReflectionService
from emoticorebot.services.light_reflection import LightReflectionService
from emoticorebot.session.manager import SessionManager


@dataclass(frozen=True)
class TurnMemoryWriteResult:
    light_insight: dict[str, Any]
    event_count: int = 0
    should_run_deep_insight: bool = False
    deep_insight_reason: str = ""


class MemoryService:
    """从运行时材料提炼 cognitive_event，并调度主脑反思。"""

    def __init__(
        self,
        workspace: Path,
        emotion_manager: EmotionStateManager,
        session_manager: SessionManager,
        memory_window: int = 100,
        reflection_llm: Any = None,
        deep_insight_decider: Callable[..., tuple[bool, str]] | None = None,
    ):
        self.workspace = workspace
        self.emotion_mgr = emotion_manager
        self.sessions = session_manager
        self.memory_window = memory_window
        self.reflection_llm = reflection_llm
        self._deep_insight_decider = deep_insight_decider
        self.light_reflection = LightReflectionService(workspace, emotion_manager, reflection_llm)
        self.deep_reflection = DeepReflectionService(workspace, reflection_llm)

    async def write_turn_memory(self, state: dict[str, Any]) -> TurnMemoryWriteResult:
        """为当前轮生成 light_insight，并把结构化认知材料写入 cognitive_event。"""
        output = str(state.get("output", "") or "").strip()
        user_input = str(state.get("user_input", "") or "").strip()
        if not output:
            return TurnMemoryWriteResult(light_insight={})

        emotion_event = self.emotion_mgr.update_from_conversation(user_input, output)
        emotion_label = self.emotion_mgr.get_emotion_label()
        importance_score = CognitiveEvent.estimate_importance(user_input, output)
        initial_snapshot = self.emotion_mgr.snapshot()
        execution_snapshot = self._extract_execution_snapshot_from_state(state)

        reflection = await self.light_reflection.reflect_turn(
            user_input=user_input,
            output=output,
            emotion_label=emotion_label,
            pad=dict(initial_snapshot.get("pad", {}) or {}),
            drives=dict(initial_snapshot.get("drives", {}) or {}),
            execution=execution_snapshot,
            executor_trace=self._extract_executor_trace(state),
        )

        current_snapshot = reflection.state_snapshot or self.emotion_mgr.snapshot()
        current_emotion_label = str(current_snapshot.get("emotion_label", emotion_label) or emotion_label)

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

        decider = self._deep_insight_decider or self._default_deep_insight_decision
        should_run_deep, deep_reason = decider(
            state=state,
            importance=importance_score,
            execution=execution_snapshot,
            light_insight=reflection.light_insight,
        )
        if should_run_deep:
            logger.debug("Scheduling deep_insight after light_insight: {}", deep_reason)

        return TurnMemoryWriteResult(
            light_insight=reflection.light_insight,
            event_count=len(events),
            should_run_deep_insight=should_run_deep,
            deep_insight_reason=deep_reason,
        )

    async def run_deep_insight(
        self,
        *,
        reason: str = "",
        warm_limit: int = 15,
    ) -> DeepReflectionResult:
        """按需触发 deep_insight，把稳定结论写入长期 memory。"""
        recent_events = CognitiveEvent.retrieve(self.workspace, query="", k=max(6, warm_limit))
        if not recent_events:
            return DeepReflectionResult()
        if reason:
            logger.debug("Running deep_insight: {}", reason)
        return await self.deep_reflection.run_cycle(recent_events)

    @staticmethod
    def _extract_executor_trace(state: dict[str, Any]) -> list[dict[str, Any]]:
        trace = state.get("executor_trace")
        if not isinstance(trace, list):
            return []
        return [dict(item) for item in trace if isinstance(item, dict)]

    @staticmethod
    def _extract_execution_snapshot_from_state(state: dict[str, Any]) -> dict[str, Any]:
        executor = state.get("executor")
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        execution_metadata = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        if executor is None and not execution_metadata:
            return {}

        summary = ""
        if executor is not None:
            summary = str(getattr(executor, "analysis", "") or "").strip()
        if not summary:
            summary = str((execution_metadata or {}).get("summary", "") or "").strip()

        try:
            confidence_value = float(
                getattr(executor, "confidence", 0.0)
                if executor is not None
                else (execution_metadata or {}).get("confidence", 0.0)
            )
        except Exception:
            confidence_value = 0.0

        return {
            "invoked": executor is not None or bool(execution_metadata),
            "thread_id": str(getattr(executor, "thread_id", "") or (execution_metadata or {}).get("thread_id", "") or "").strip(),
            "run_id": str(getattr(executor, "run_id", "") or (execution_metadata or {}).get("run_id", "") or "").strip(),
            "control_state": str(getattr(executor, "control_state", "") or (execution_metadata or {}).get("control_state", "idle") or "idle").strip(),
            "status": str(getattr(executor, "status", "") or (execution_metadata or {}).get("status", "none") or "none").strip(),
            "summary": summary,
            "missing": list(getattr(executor, "missing", []) or (execution_metadata or {}).get("missing", []) or []),
            "pending_review": dict(getattr(executor, "pending_review", {}) or (execution_metadata or {}).get("pending_review", {}) or {}),
            "recommended_action": str(
                getattr(executor, "recommended_action", "")
                or (execution_metadata or {}).get("recommended_action", "")
                or ""
            ).strip(),
            "confidence": confidence_value,
        }

    @staticmethod
    def _default_deep_insight_decision(
        *,
        state: dict[str, Any],
        importance: float,
        execution: dict[str, Any],
        light_insight: dict[str, Any],
    ) -> tuple[bool, str]:
        main_brain = state.get("main_brain")
        execution_review = (
            light_insight.get("execution_review")
            if isinstance(light_insight, dict) and isinstance(light_insight.get("execution_review"), dict)
            else {}
        )
        control_state = str(execution.get("control_state", "") or "").strip().lower()
        status = str(execution.get("status", "") or "").strip().lower()
        missing = [str(item).strip() for item in list(execution.get("missing", []) or []) if str(item).strip()]
        pending_review = execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {}
        effectiveness = str((execution_review or {}).get("effectiveness", "none") or "none").strip().lower()
        failure_reason = str((execution_review or {}).get("failure_reason", "") or "").strip()
        relation_shift = str(light_insight.get("relation_shift", "") or "").strip().lower() if isinstance(light_insight, dict) else ""
        direct_updates = light_insight.get("direct_updates") if isinstance(light_insight, dict) and isinstance(light_insight.get("direct_updates"), dict) else {}
        user_profile = list(direct_updates.get("user_profile", []) or []) if isinstance(direct_updates, dict) else []
        soul_preferences = list(direct_updates.get("soul_preferences", []) or []) if isinstance(direct_updates, dict) else []
        execution_reason = str(getattr(main_brain, "execution_reason", "") or "").strip() if main_brain is not None else ""

        if execution.get("invoked") and (status in {"failed", "need_more"} or control_state == "paused"):
            return True, f"execution_requires_followup:{control_state or status}"
        if execution.get("invoked") and (missing or pending_review):
            return True, "execution_blocked_or_waiting_review"
        if execution.get("invoked") and effectiveness in {"low", "medium"} and failure_reason:
            return True, f"execution_review:{failure_reason}"
        if importance >= 0.82 and relation_shift in {"trust_up", "trust_down"}:
            return True, "high_importance_relation_shift"
        if importance >= 0.82 and (user_profile or soul_preferences):
            return True, "high_importance_direct_updates"
        if execution_reason in {
            "loop_limit_reached",
            "main_brain_requested_executor_followup",
            "executor_waiting_for_user_input",
        }:
            return True, f"main_brain_signal:{execution_reason}"
        return False, ""


__all__ = ["MemoryService", "TurnMemoryWriteResult"]
