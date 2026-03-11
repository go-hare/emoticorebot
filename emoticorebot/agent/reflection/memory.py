"""Turn reflection and deep reflection orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig

from loguru import logger

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.memory import MemoryStore
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.agent.reflection.deep import DeepReflectionResult, DeepReflectionService
from emoticorebot.agent.reflection.turn import TurnReflectionService
from emoticorebot.session.manager import SessionManager


@dataclass(frozen=True)
class TurnReflectionWriteResult:
    turn_reflection: dict[str, Any] = field(default_factory=dict)
    event_count: int = 0
    memory_ids: list[str] = field(default_factory=list)
    memory_count: int = 0
    updated_user: bool = False
    updated_soul: bool = False
    updated_state: bool = False
    state_snapshot: dict[str, Any] | None = None
    should_run_deep_reflection: bool = False
    deep_reflection_reason: str = ""


class MemoryService:
    """Persist session -> cognitive_event -> turn_reflection -> memory."""

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
        session_manager: SessionManager,
        memory_window: int = 100,
        reflection_llm: Any = None,
        deep_reflection_decider: Callable[..., tuple[bool, str]] | None = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ):
        self.workspace = workspace
        self.emotion_mgr = emotion_manager
        self.sessions = session_manager
        self.memory_window = memory_window
        self.turn_reflection = TurnReflectionService(emotion_manager, reflection_llm)
        self.deep_reflection = DeepReflectionService(
            workspace,
            reflection_llm,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self.memory_store = MemoryStore(
            workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self._deep_reflection_decider = deep_reflection_decider

    async def write_turn_reflection(self, state: dict[str, Any]) -> TurnReflectionWriteResult:
        output = str(state.get("output", "") or "").strip()
        user_input = str(state.get("user_input", "") or "").strip()
        if not output:
            return TurnReflectionWriteResult()

        self.emotion_mgr.update_from_conversation(user_input, output)
        snapshot = self.emotion_mgr.snapshot()
        importance = CognitiveEvent.estimate_importance(user_input, output)
        task = self._extract_task_snapshot_from_state(state)

        reflection = await self.turn_reflection.reflect_turn(
            user_input=user_input,
            output=output,
            emotion_label=str(snapshot.get("emotion_label", "平静") or "平静"),
            pad=dict(snapshot.get("pad", {}) or {}),
            drives=dict(snapshot.get("drives", {}) or {}),
            execution=task,
        )

        updated_user, updated_soul, updated_state, state_snapshot = self._apply_turn_reflection_direct_updates(
            reflection.turn_reflection
        )

        events = CognitiveEvent.build_turn_events(
            state=state,
            importance=importance,
            turn_reflection=reflection.turn_reflection,
        )
        for event in events:
            CognitiveEvent.append(self.workspace, event)
        event_ids = [event.id for event in events]

        memory_ids = self.memory_store.append_many(
            self._prepare_turn_memory_candidates(
                state=state,
                turn_reflection=reflection.turn_reflection,
                event_ids=event_ids,
            )
        )

        decider = self._deep_reflection_decider or self._default_deep_reflection_decision
        should_run_deep_reflection, deep_reflection_reason = decider(
            state=state,
            importance=importance,
            task=task,
            turn_reflection=reflection.turn_reflection,
        )
        if should_run_deep_reflection:
            logger.debug("Scheduling deep_reflection after turn_reflection: {}", deep_reflection_reason)

        return TurnReflectionWriteResult(
            turn_reflection=reflection.turn_reflection,
            event_count=len(events),
            memory_ids=memory_ids,
            memory_count=len(memory_ids),
            updated_user=updated_user,
            updated_soul=updated_soul,
            updated_state=updated_state,
            state_snapshot=state_snapshot or reflection.state_snapshot,
            should_run_deep_reflection=should_run_deep_reflection,
            deep_reflection_reason=deep_reflection_reason,
        )

    async def run_deep_reflection(
        self,
        *,
        reason: str = "",
        warm_limit: int = 15,
    ) -> DeepReflectionResult:
        recent_events = CognitiveEvent.retrieve(self.workspace, query="", k=max(6, warm_limit))
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
        state_update = payload if isinstance(payload, dict) else {}
        pad_delta = self._normalize_delta_map(
            state_update.get("pad_delta"),
            allowed=("pleasure", "arousal", "dominance"),
            max_abs=0.3,
        )
        drive_delta = self._normalize_delta_map(
            state_update.get("drives_delta"),
            allowed=("social", "energy"),
            max_abs=20.0,
        )
        should_apply = bool(state_update.get("should_apply", False)) or bool(pad_delta or drive_delta)
        if not should_apply:
            return False, None
        try:
            confidence = float(state_update.get("confidence", 0.0) or 0.0)
        except Exception:
            confidence = 0.0
        if confidence < self._TURN_STATE_THRESHOLD:
            return False, None
        if not pad_delta and not drive_delta:
            return False, None
        snapshot = self.emotion_mgr.apply_reflection_state_update(
            pad_delta=pad_delta,
            drive_delta=drive_delta,
        )
        return True, snapshot

    @staticmethod
    def _normalize_delta_map(
        payload: Any,
        *,
        allowed: tuple[str, ...],
        max_abs: float,
    ) -> dict[str, float]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, float] = {}
        precision = 3 if max_abs <= 1.0 else 2
        for key in allowed:
            if key not in payload:
                continue
            try:
                value = float(payload.get(key, 0.0) or 0.0)
            except Exception:
                continue
            value = max(-max_abs, min(max_abs, value))
            if abs(value) > 1e-6:
                normalized[key] = round(value, precision)
        return normalized

    def _prepare_turn_memory_candidates(
        self,
        *,
        state: dict[str, Any],
        turn_reflection: dict[str, Any],
        event_ids: list[str],
    ) -> list[dict[str, Any]]:
        candidates = turn_reflection.get("memory_candidates") if isinstance(turn_reflection, dict) else []
        if not isinstance(candidates, list):
            return []

        session_id = str(state.get("session_id", "") or "")
        message_id = str(((state.get("metadata") or {}).get("message_id", "")) or "").strip()
        turn_id = f"turn_{message_id}" if message_id else ""
        tool_names = self._extract_tool_names(state)

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
    def _extract_tool_names(state: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for item in list(state.get("task_trace", []) or []):
            if not isinstance(item, dict):
                continue
            for key in ("tool_name", "tool", "name", "node"):
                value = str(item.get(key, "") or "").strip()
                if value and value not in names:
                    names.append(value)
        return names[:6]

    @staticmethod
    def _extract_task_snapshot_from_state(state: dict[str, Any]) -> dict[str, Any]:
        task = state.get("task")
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        task_metadata = metadata.get("task") if isinstance(metadata.get("task"), dict) else {}
        if not task_metadata:
            task_metadata = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        if task is None and not task_metadata:
            return {}

        summary = ""
        if task is not None:
            summary = str(getattr(task, "analysis", "") or "").strip()
        if not summary:
            summary = str(task_metadata.get("summary", "") or "").strip()

        try:
            confidence_value = float(
                getattr(task, "confidence", 0.0)
                if task is not None
                else task_metadata.get("confidence", 0.0)
            )
        except Exception:
            confidence_value = 0.0

        return {
            "invoked": task is not None or bool(task_metadata),
            "thread_id": str(
                (getattr(task, "thread_id", "") if task is not None else "")
                or task_metadata.get("thread_id", "")
            ).strip(),
            "run_id": str(
                (getattr(task, "run_id", "") if task is not None else "")
                or task_metadata.get("run_id", "")
            ).strip(),
            "control_state": str(
                (getattr(task, "control_state", "") if task is not None else "")
                or task_metadata.get("control_state", "idle")
            ).strip(),
            "status": str(
                (getattr(task, "status", "") if task is not None else "")
                or task_metadata.get("status", "none")
            ).strip(),
            "summary": summary,
            "missing": list(
                (getattr(task, "missing", []) if task is not None else []) or task_metadata.get("missing", []) or []
            ),
            "pending_review": dict(
                (getattr(task, "pending_review", {}) if task is not None else {})
                or task_metadata.get("pending_review", {})
                or {}
            ),
            "recommended_action": str(
                (getattr(task, "recommended_action", "") if task is not None else "")
                or task_metadata.get("recommended_action", "")
            ).strip(),
            "confidence": confidence_value,
            "attempt_count": int(getattr(task, "attempts", 0) if task is not None else 0),
        }

    @staticmethod
    def _default_deep_reflection_decision(
        *,
        state: dict[str, Any],
        importance: float,
        task: dict[str, Any],
        turn_reflection: dict[str, Any],
    ) -> tuple[bool, str]:
        brain = state.get("brain")
        execution_review = (
            turn_reflection.get("execution_review")
            if isinstance(turn_reflection, dict) and isinstance(turn_reflection.get("execution_review"), dict)
            else {}
        )
        status = str(task.get("status", "") or "").strip().lower()
        control_state = str(task.get("control_state", "") or "").strip().lower()
        missing = [str(item).strip() for item in list(task.get("missing", []) or []) if str(item).strip()]
        pending_review = task.get("pending_review") if isinstance(task.get("pending_review"), dict) else {}
        effectiveness = str((execution_review or {}).get("effectiveness", "none") or "none").strip().lower()
        failure_reason = str((execution_review or {}).get("main_failure_reason", "") or "").strip()
        user_updates = [str(item).strip() for item in list(turn_reflection.get("user_updates", []) or []) if str(item).strip()]
        soul_updates = [str(item).strip() for item in list(turn_reflection.get("soul_updates", []) or []) if str(item).strip()]
        memory_candidates = list(turn_reflection.get("memory_candidates", []) or []) if isinstance(turn_reflection, dict) else []
        task_reason = str(getattr(brain, "task_reason", "") or "").strip() if brain is not None else ""

        if task.get("invoked") and (status in {"failed", "need_more"} or control_state == "paused"):
            return True, f"task_requires_followup:{control_state or status}"
        if task.get("invoked") and (missing or pending_review):
            return True, "task_blocked_or_waiting_review"
        if task.get("invoked") and effectiveness in {"low", "medium"} and failure_reason:
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


__all__ = ["MemoryService", "TurnReflectionWriteResult"]

