from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class CognitiveEvent:
    id: str
    version: str
    timestamp: str
    session_id: str
    turn_id: str
    user_input: str
    brain_state: dict[str, Any] = field(default_factory=dict)
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
            emotion = str(((row.get("brain_state") or {}).get("emotion", "") or "平静")).strip()
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
        state: dict[str, Any],
        importance: float,
        turn_reflection: dict[str, Any] | None = None,
    ) -> list["CognitiveEvent"]:
        user_input = str(state.get("user_input", "") or "").strip()
        assistant_output = str(state.get("output", "") or "").strip()
        if not assistant_output:
            return []

        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        brain = state.get("brain")
        event = cls(
            id=f"evt_{uuid4().hex}",
            version="3",
            timestamp=datetime.now().astimezone().isoformat(),
            session_id=str(state.get("session_id", "") or ""),
            turn_id=cls._extract_turn_id(state),
            user_input=user_input,
            brain_state=cls._build_brain_state(brain),
            retrieval=cls._build_retrieval(brain=brain, user_input=user_input),
            task=cls._build_task_state(state),
            assistant_output=assistant_output,
            turn_reflection=cls._normalize_turn_reflection(turn_reflection),
            meta={
                "importance": round(float(importance), 2),
                "channel": str(state.get("channel", "") or ""),
                "source": "brain.turn_reflection",
                "message_id": str(metadata.get("message_id", "") or ""),
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
    def _extract_turn_id(state: dict[str, Any]) -> str:
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        message_id = str(metadata.get("message_id", "") or "").strip()
        if message_id:
            return f"turn_{message_id}"
        return f"turn_{uuid4().hex[:12]}"

    @staticmethod
    def _build_brain_state(brain: Any) -> dict[str, Any]:
        if brain is None:
            return {}
        def _brain_get(key: str, default: Any = "") -> Any:
            if isinstance(brain, dict):
                return brain.get(key, default)
            return getattr(brain, key, default)
        return {
            "emotion": str(_brain_get("emotion", "") or "平静").strip() or "平静",
            "pad": dict(_brain_get("pad", {}) or {}),
            "intent": str(_brain_get("intent", "") or "").strip(),
            "working_hypothesis": str(_brain_get("working_hypothesis", "") or "").strip(),
            "retrieval_query": str(_brain_get("retrieval_query", "") or "").strip(),
            "retrieval_focus": [
                str(item).strip()
                for item in list(_brain_get("retrieval_focus", []) or [])
                if str(item).strip()
            ],
            "retrieved_memory_ids": [
                str(item).strip()
                for item in list(_brain_get("retrieved_memory_ids", []) or [])
                if str(item).strip()
            ],
            "task_request": str(_brain_get("task_brief", "") or "").strip(),
            "task_action": str(_brain_get("task_action", "") or "").strip(),
            "task_reason": str(_brain_get("task_reason", "") or "").strip(),
            "final_decision": str(_brain_get("final_decision", "") or "").strip(),
        }

    @staticmethod
    def _build_retrieval(*, brain: Any, user_input: str) -> dict[str, Any]:
        def _brain_get(key: str, default: Any = "") -> Any:
            if isinstance(brain, dict):
                return brain.get(key, default)
            return getattr(brain, key, default)
        query = str(_brain_get("retrieval_query", "") or "").strip() if brain is not None else ""
        if not query:
            query = user_input
        memory_ids = []
        if brain is not None:
            memory_ids = [
                str(item).strip()
                for item in list(_brain_get("retrieved_memory_ids", []) or [])
                if str(item).strip()
            ]
        return {"query": query, "memory_ids": memory_ids}

    @staticmethod
    def _build_task_state(state: dict[str, Any]) -> dict[str, Any]:
        task = state.get("task")
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}

        task_dict: dict[str, Any] = {}
        if isinstance(task, dict):
            task_dict = task
        elif task is not None:
            task_dict = {
                "summary": str(getattr(task, "summary", "") or getattr(task, "analysis", "") or ""),
                "status": str(getattr(task, "status", "") or ""),
                "control_state": str(getattr(task, "control_state", "") or ""),
                "missing": list(getattr(task, "missing", []) or []),
            }

        summary = str(task_dict.get("summary", "") or task_dict.get("analysis", "") or "").strip()
        status = str(task_dict.get("status", "") or "none").strip() or "none"
        result_status = str(task_dict.get("result_status", "") or execution.get("result_status", "") or "").strip()
        if not summary:
            summary = str(execution.get("summary", "") or "").strip()
        if status == "none":
            status = str(execution.get("status", "") or "none").strip() or "none"

        control_state = str(
            task_dict.get("control_state", "")
            or execution.get("control_state", "")
            or "idle"
        ).strip()

        raw_missing = list(task_dict.get("missing", []) or execution.get("missing", []) or [])
        missing = [str(item).strip() for item in raw_missing if str(item).strip()]

        task_action = str(execution.get("task_action", "") or "").strip()
        used = bool(task_dict) or task_action in {"create_task", "fill_task"}

        return {
            "used": used,
            "status": status,
            "result_status": result_status,
            "summary": summary,
            "control_state": control_state,
            "missing": missing,
        }

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
    def _compact(text: str, *, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"


__all__ = ["CognitiveEvent"]
