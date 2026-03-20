from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from emoticorebot.types import ReflectionInput

from emoticorebot.utils.execution_projection import project_task_for_memory


@dataclass
class CognitiveEvent:
    id: str
    schema_version: str
    timestamp: str
    session_id: str
    turn_id: str
    user_input: str
    main_brain_state: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    task: dict[str, Any] = field(default_factory=dict)
    assistant_output: str = ""
    turn_reflection: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def storage_path(cls, workspace: Path) -> Path:
        path = workspace / "memory" / "cognitive_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def append(cls, workspace: Path, event: "CognitiveEvent") -> None:
        with cls.storage_path(workspace).open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def read_all(cls, workspace: Path) -> list[dict[str, Any]]:
        path = cls.storage_path(workspace)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file_obj:
            for raw_line in file_obj:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        return records

    @classmethod
    def retrieve(cls, workspace: Path, query: str = "", *, k: int = 8) -> list[dict[str, Any]]:
        records = cls.read_all(workspace)
        ranked = sorted(records, key=lambda item: cls._score(item, query=query), reverse=True)
        if k <= 0:
            return ranked
        return ranked[:k]

    @classmethod
    def recent(cls, workspace: Path, *, limit: int = 8) -> list[dict[str, Any]]:
        records = cls.read_all(workspace)
        ranked = sorted(records, key=cls._timestamp_score)
        if limit <= 0:
            return ranked
        return ranked[-limit:]

    @classmethod
    def build_cognitive_sections(
        cls,
        workspace: Path,
        *,
        query: str = "",
        current_emotion: str = "",
        pad_state: tuple[float, float, float] | None = None,
    ) -> list[str]:
        del current_emotion, pad_state
        rows = cls.retrieve(workspace, query=query, k=4)
        if not rows:
            return []

        lines: list[str] = []
        for row in rows:
            summary = cls._compact(
                str(
                    ((row.get("turn_reflection") or {}).get("summary"))
                    or row.get("assistant_output", "")
                    or row.get("user_input", "")
                ),
                limit=120,
            )
            if not summary:
                continue
            emotion = str(((row.get("main_brain_state") or {}).get("emotion", "") or "平静")).strip()
            outcome_raw = str(((row.get("turn_reflection") or {}).get("outcome", "") or "unknown")).strip()
            outcome = {
                "success": "成功",
                "partial": "部分完成",
                "failed": "失败",
                "no_execution": "未执行",
                "unknown": "未知",
            }.get(outcome_raw, outcome_raw or "未知")
            used_task = bool((row.get("task") or {}).get("used"))
            importance = float((row.get("meta") or {}).get("importance", 0.5) or 0.5)
            mode = "执行" if used_task else "直答"
            lines.append(f"- [{emotion}|{mode}|{outcome}|{importance:.2f}] {summary}")

        if not lines:
            return []
        return ["## 最近认知事件\n" + "\n".join(lines)]

    @classmethod
    def build_turn_events(
        cls,
        *,
        reflection_input: ReflectionInput,
        importance: float,
        turn_reflection: dict[str, Any] | None = None,
    ) -> list["CognitiveEvent"]:
        user_input = str(reflection_input.get("user_input", "") or "").strip()
        assistant_output = str(
            reflection_input.get("assistant_output", "") or reflection_input.get("output", "") or ""
        ).strip()
        if not assistant_output:
            return []

        main_brain = reflection_input.get("main_brain")
        emotion = reflection_input.get("emotion") if isinstance(reflection_input.get("emotion"), dict) else {}
        event = cls(
            id=f"evt_{uuid4().hex}",
            schema_version="cognitive_event.v1",
            timestamp=datetime.now().astimezone().isoformat(),
            session_id=str(reflection_input.get("session_id", "") or ""),
            turn_id=cls._extract_turn_id(reflection_input),
            user_input=user_input,
            main_brain_state=cls._build_main_brain_state(main_brain, emotion=emotion),
            retrieval=cls._build_retrieval(main_brain=main_brain, user_input=user_input),
            task=cls._build_task_state(reflection_input),
            assistant_output=assistant_output,
            turn_reflection=cls._normalize_turn_reflection(turn_reflection),
            meta={
                "importance": round(float(importance), 2),
                "channel": str(reflection_input.get("channel", "") or ""),
                "source": "main_brain.turn_reflection",
                "source_type": str(reflection_input.get("source_type", "user_turn") or "user_turn"),
                "message_id": str(reflection_input.get("message_id", "") or ""),
            },
        )
        return [event]

    @staticmethod
    def estimate_importance(user_input: str, output: str) -> float:
        text = f"{user_input} {output}".lower()
        score = 0.42
        if any(token in text for token in ["帮", "计划", "提醒", "错误", "失败", "问题", "焦虑", "喜欢", "重要"]):
            score += 0.18
        if any(token in text for token in ["明天", "下周", "长期", "记住", "以后", "风格", "偏好"]):
            score += 0.16
        if "?" in text or "？" in text:
            score += 0.08
        if len(text) >= 120:
            score += 0.08
        return max(0.1, min(1.0, score))

    @staticmethod
    def _extract_turn_id(reflection_input: ReflectionInput) -> str:
        turn_id = str(reflection_input.get("turn_id", "") or "").strip()
        if turn_id:
            return turn_id
        message_id = str(reflection_input.get("message_id", "") or "").strip()
        if message_id:
            return f"turn_{message_id}"
        return f"turn_{uuid4().hex[:12]}"

    @staticmethod
    def _build_main_brain_state(
        main_brain: Any,
        *,
        emotion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if main_brain is None:
            main_brain = {}

        emotion_payload = emotion if isinstance(emotion, dict) else {}

        def _get_main_brain_value(key: str, default: Any = "") -> Any:
            if isinstance(main_brain, dict):
                return main_brain.get(key, default)
            return getattr(main_brain, key, default)

        task_payload = _get_main_brain_value("task", {})
        task_request = ""
        if isinstance(task_payload, dict):
            task_request = str(task_payload.get("request", "") or task_payload.get("goal", "") or "").strip()
        if not task_request:
            task_request = str(_get_main_brain_value("task_request", "") or "").strip()

        main_brain_state = {
            "emotion": str(
                emotion_payload.get("emotion_label", "") or _get_main_brain_value("emotion", "") or "平静"
            ).strip()
            or "平静",
            "pad": dict(emotion_payload.get("pad", {}) or _get_main_brain_value("pad", {}) or {}),
            "drives": dict(emotion_payload.get("drives", {}) or _get_main_brain_value("drives", {}) or {}),
            "emotion_prompt": str(
                emotion_payload.get("emotion_prompt", "") or _get_main_brain_value("emotion_prompt", "") or ""
            ).strip(),
            "intent": str(_get_main_brain_value("intent", "") or "").strip(),
            "working_hypothesis": str(_get_main_brain_value("working_hypothesis", "") or "").strip(),
            "retrieval_query": str(_get_main_brain_value("retrieval_query", "") or "").strip(),
            "retrieval_focus": [
                str(item).strip()
                for item in list(_get_main_brain_value("retrieval_focus", []) or [])
                if str(item).strip()
            ],
            "retrieved_memory_ids": [
                str(item).strip()
                for item in list(_get_main_brain_value("retrieved_memory_ids", []) or [])
                if str(item).strip()
            ],
            "task_request": task_request,
            "task_action": str(_get_main_brain_value("task_action", "") or "").strip(),
            "task_reason": str(_get_main_brain_value("task_reason", "") or "").strip(),
        }
        return main_brain_state

    @staticmethod
    def _build_retrieval(*, main_brain: Any, user_input: str) -> dict[str, Any]:
        def _get_main_brain_value(key: str, default: Any = "") -> Any:
            if isinstance(main_brain, dict):
                return main_brain.get(key, default)
            return getattr(main_brain, key, default)

        query = str(_get_main_brain_value("retrieval_query", "") or "").strip() if main_brain is not None else ""
        if not query:
            query = user_input
        memory_ids = []
        if main_brain is not None:
            memory_ids = [
                str(item).strip()
                for item in list(_get_main_brain_value("retrieved_memory_ids", []) or [])
                if str(item).strip()
            ]
        return {"query": query, "memory_ids": memory_ids}

    @staticmethod
    def _build_task_state(reflection_input: ReflectionInput) -> dict[str, Any]:
        task = reflection_input.get("task") if isinstance(reflection_input.get("task"), dict) else {}
        execution = (
            reflection_input.get("execution") if isinstance(reflection_input.get("execution"), dict) else {}
        )
        return project_task_for_memory(task, execution=execution)

    @staticmethod
    def _normalize_turn_reflection(value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "summary": str(value.get("summary", "") or "").strip(),
            "problems": [str(item).strip() for item in list(value.get("problems", []) or []) if str(item).strip()],
            "resolution": str(value.get("resolution", "") or "").strip(),
            "outcome": str(value.get("outcome", "") or "").strip(),
            "next_hint": str(value.get("next_hint", "") or "").strip(),
            "user_updates": [
                str(item).strip() for item in list(value.get("user_updates", []) or []) if str(item).strip()
            ],
            "soul_updates": [
                str(item).strip() for item in list(value.get("soul_updates", []) or []) if str(item).strip()
            ],
            "state_update": dict(value.get("state_update", {}) or {}),
            "memory_candidates": list(value.get("memory_candidates", []) or []),
            "execution_review": dict(value.get("execution_review", {}) or {}),
        }

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        tokens = [token for token in "".join(ch if ch.isalnum() or "\u4e00" <= ch <= "\u9fff" else " " for ch in str(text or "").lower()).split() if token]
        return set(tokens)

    @classmethod
    def _score(cls, item: dict[str, Any], *, query: str) -> float:
        summary = str(((item.get("turn_reflection") or {}).get("summary", "")) or "")
        combined = " ".join(
            [
                str(item.get("user_input", "") or ""),
                str(item.get("assistant_output", "") or ""),
                summary,
                str(((item.get("task") or {}).get("summary", "")) or ""),
            ]
        )
        importance = float((item.get("meta") or {}).get("importance", 0.5) or 0.5)
        query_tokens = cls._tokenize(query)
        text_tokens = cls._tokenize(combined)
        overlap = len(query_tokens & text_tokens)
        return importance + overlap * 0.2

    @staticmethod
    def _timestamp_score(item: dict[str, Any]) -> float:
        raw_value = str(item.get("timestamp", "") or "").strip()
        if not raw_value:
            return 0.0
        try:
            return datetime.fromisoformat(raw_value).timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _compact(text: str, *, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"


__all__ = ["CognitiveEvent"]

