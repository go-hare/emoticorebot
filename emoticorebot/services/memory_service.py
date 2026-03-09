"""Memory Service - 记忆管理服务。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.services.light_reflection import LightReflectionService
from emoticorebot.session.manager import SessionManager


class MemoryService:
    """记忆管理服务：写入认知事件流与执行续跑记忆。"""

    def __init__(
        self,
        workspace: Path,
        emotion_manager: EmotionStateManager,
        session_manager: SessionManager,
        memory_window: int = 100,
        reflection_llm: Any = None,
    ):
        self.workspace = workspace
        self.emotion_mgr = emotion_manager
        self.sessions = session_manager
        self.memory_window = memory_window
        self.reflection_llm = reflection_llm
        self.light_reflection = LightReflectionService(workspace, emotion_manager, reflection_llm)

    async def write_turn_memory(self, state: dict[str, Any]) -> None:
        """写入单轮认知事件流与执行续跑摘要。"""
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

        task_entry = self._build_task_memory_entry_from_state(state)
        if task_entry is not None:
            self._append_task_memory_entry(task_entry)

    def append_execution_memory(
        self,
        *,
        session_id: str,
        turn_id: str,
        execution: dict[str, Any],
        channel: str = "",
        source: str = "runtime_control",
        event: str = "execution.snapshot",
        main_brain: dict[str, Any] | None = None,
    ) -> None:
        entry = self._build_task_memory_entry(
            session_id=session_id,
            turn_id=turn_id,
            execution=execution,
            channel=channel,
            source=source,
            event=event,
            main_brain=main_brain,
        )
        if entry is not None:
            self._append_task_memory_entry(entry)

    def _build_task_memory_entry_from_state(self, state: dict[str, Any]) -> dict[str, Any] | None:
        executor = state.get("executor")
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        execution_metadata = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
        if executor is None and not execution_metadata:
            return None

        summary = ""
        if executor is not None:
            summary = str(getattr(executor, "analysis", "") or "").strip()
        if not summary:
            summary = str((execution_metadata or {}).get("summary", "") or "").strip()

        execution = {
            "invoked": executor is not None or bool(execution_metadata),
            "thread_id": str(getattr(executor, "thread_id", "") or (execution_metadata or {}).get("thread_id", "") or "").strip(),
            "run_id": str(getattr(executor, "run_id", "") or (execution_metadata or {}).get("run_id", "") or "").strip(),
            "control_state": str(getattr(executor, "control_state", "") or (execution_metadata or {}).get("control_state", "idle") or "idle").strip(),
            "status": str(getattr(executor, "status", "") or (execution_metadata or {}).get("status", "none") or "none").strip(),
            "summary": summary,
            "missing": list(getattr(executor, "missing", []) or (execution_metadata or {}).get("missing", []) or []),
            "pending_review": dict(getattr(executor, "pending_review", {}) or (execution_metadata or {}).get("pending_review", {}) or {}),
            "recommended_action": str(getattr(executor, "recommended_action", "") or "").strip(),
            "confidence": float(getattr(executor, "confidence", 0.0) or 0.0) if executor is not None else float((execution_metadata or {}).get("confidence", 0.0) or 0.0),
        }
        main_brain = self._build_main_brain_memory_slice(state.get("main_brain"))
        return self._build_task_memory_entry(
            session_id=str(state.get("session_id", "") or "").strip(),
            turn_id=self._extract_turn_id(state),
            execution=execution,
            channel=str(state.get("channel", "") or "").strip(),
            source="turn_memory",
            event=self._build_task_event_name(execution),
            main_brain=main_brain,
        )

    def _build_task_memory_entry(
        self,
        *,
        session_id: str,
        turn_id: str,
        execution: dict[str, Any],
        channel: str,
        source: str,
        event: str,
        main_brain: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not self._should_persist_execution(execution):
            return None

        normalized_missing = [
            str(item).strip()
            for item in (execution.get("missing", []) if isinstance(execution.get("missing", []), list) else [])
            if str(item).strip()
        ]
        pending_review = execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {}
        summary = str(execution.get("summary", "") or "").strip()
        task_id = str(execution.get("run_id", "") or execution.get("thread_id", "") or turn_id).strip()
        main_brain_payload = dict(main_brain or {})
        execution_action = str(main_brain_payload.get("execution_action", "") or "").strip()
        execution_reason = str(main_brain_payload.get("execution_reason", "") or "").strip()
        next_hint = self._build_next_hint(execution, main_brain=main_brain_payload)
        blocking_points = self._build_blocking_points(
            execution,
            pending_review=pending_review,
            missing_inputs=normalized_missing,
            next_hint=next_hint,
        )
        normalized_status = self._normalize_task_memory_status(execution)
        entry: dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "session_id": session_id,
            "turn_id": turn_id,
            "task_id": task_id,
            "status": normalized_status,
            "summary": summary,
            "blocking_points": blocking_points,
            "missing_inputs": normalized_missing,
            "next_hint": next_hint,
        }
        return {key: value for key, value in entry.items() if value not in ("", [], {}, None)}

    def _append_task_memory_entry(self, payload: dict[str, Any]) -> None:
        path = self.task_memory_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def task_memory_path(self) -> Path:
        path = self.workspace / "memory" / "task_memory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _should_persist_execution(execution: dict[str, Any]) -> bool:
        return bool(
            execution
            and (
                str(execution.get("thread_id", "") or "").strip()
                or str(execution.get("run_id", "") or "").strip()
                or str(execution.get("summary", "") or "").strip()
                or list(execution.get("missing", []) or [])
                or dict(execution.get("pending_review", {}) or {})
                or str(execution.get("control_state", "") or "idle").strip() != "idle"
            )
        )

    @staticmethod
    def _build_task_event_name(execution: dict[str, Any]) -> str:
        control_state = str(execution.get("control_state", "") or "completed").strip() or "completed"
        status = str(execution.get("status", "") or "none").strip() or "none"
        return f"execution.{control_state}.{status}"

    @staticmethod
    def _normalize_task_memory_status(execution: dict[str, Any]) -> str:
        control_state = str(execution.get("control_state", "") or "idle").strip().lower()
        status = str(execution.get("status", "") or "none").strip().lower()
        pending_review = execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {}
        action_requests = pending_review.get("action_requests") if isinstance(pending_review.get("action_requests"), list) else []
        missing = execution.get("missing") if isinstance(execution.get("missing"), list) else []

        if status == "failed" or control_state == "stopped":
            return "failed"
        if control_state == "paused" or action_requests or missing:
            return "suspended"
        if status == "need_more":
            return "need_more"
        if status == "done" or control_state == "completed":
            return "done"
        return "suspended" if control_state in {"running", "paused"} else "done"

    @staticmethod
    def _build_blocking_points(
        execution: dict[str, Any],
        *,
        pending_review: dict[str, Any],
        missing_inputs: list[str],
        next_hint: str,
    ) -> list[str]:
        points: list[str] = []
        action_requests = pending_review.get("action_requests") if isinstance(pending_review.get("action_requests"), list) else []
        names = [
            str(item.get("name", "") or "").strip()
            for item in action_requests
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        ]
        if names:
            points.append(f"等待审批：{', '.join(names[:4])}")
        if missing_inputs:
            points.append(f"缺少输入：{', '.join(missing_inputs[:4])}")

        status = str(execution.get("status", "") or "").strip().lower()
        control_state = str(execution.get("control_state", "") or "").strip().lower()
        summary = str(execution.get("summary", "") or "").strip()
        if status == "failed" and summary:
            points.append(summary)
        elif control_state == "paused" and summary:
            points.append(summary)
        elif status == "need_more" and next_hint and next_hint not in points:
            points.append(next_hint)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in points:
            text = str(item).strip()
            if text and text not in seen:
                deduped.append(text)
                seen.add(text)
        return deduped[:4]

    @staticmethod
    def _build_next_hint(execution: dict[str, Any], *, main_brain: dict[str, Any] | None = None) -> str:
        main_brain = main_brain or {}
        execution_action = str(main_brain.get("execution_action", "") or "").strip()
        execution_reason = str(main_brain.get("execution_reason", "") or "").strip()
        if execution_action == "pause":
            if execution_reason == "user_requested_pause":
                return "保持暂停，等待用户明确说继续。"
            if execution_reason == "main_brain_prioritized_companionship_or_explanation":
                return "先处理陪伴或解释，再根据用户意愿恢复执行。"
            if execution_reason == "user_switched_priority":
                return "先处理更高优先级任务，完成后恢复当前执行。"
            return "主脑暂时保持挂起，等待新的恢复信号。"
        if execution_action == "stop":
            return "当前执行已停止；如需继续，请由主脑重新启动或基于保留状态恢复。"

        pending_review = execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {}
        action_requests = pending_review.get("action_requests") if isinstance(pending_review.get("action_requests"), list) else []
        if action_requests:
            names = [
                str(item.get("name", "") or "").strip()
                for item in action_requests
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            ]
            if names:
                return f"等待审批后恢复：{', '.join(names)}"

        missing = [
            str(item).strip()
            for item in (execution.get("missing", []) if isinstance(execution.get("missing", []), list) else [])
            if str(item).strip()
        ]
        if missing:
            return f"补充这些信息后可继续：{', '.join(missing[:4])}"

        control_state = str(execution.get("control_state", "") or "").strip()
        status = str(execution.get("status", "") or "").strip()
        if control_state == "paused":
            return str(execution.get("summary", "") or "等待恢复输入").strip()
        if control_state == "completed" and status == "need_more":
            return "继续补证据、补参数或追加下一步执行。"
        return ""

    @staticmethod
    @staticmethod
    def _build_main_brain_memory_slice(main_brain: Any) -> dict[str, Any]:
        if main_brain is None:
            return {}
        getter = main_brain.get if isinstance(main_brain, dict) else lambda key, default="": getattr(main_brain, key, default)
        payload = {
            "intent": str(getter("intent", "") or "").strip(),
            "working_hypothesis": str(getter("working_hypothesis", "") or "").strip(),
            "final_decision": str(getter("final_decision", "") or "").strip(),
            "question_to_executor": str(getter("question_to_executor", "") or "").strip(),
            "execution_action": str(getter("execution_action", "") or "").strip(),
            "execution_reason": str(getter("execution_reason", "") or "").strip(),
        }
        return {key: value for key, value in payload.items() if value}

    @staticmethod
    def _extract_turn_id(state: dict[str, Any]) -> str:
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        message_id = str(metadata.get("message_id", "") or state.get("message_id", "") or state.get("turn_id", "") or "").strip()
        if message_id:
            return message_id
        return f"turn_{datetime.now().strftime('%Y%m%d%H%M%S')}"


__all__ = ["MemoryService"]
