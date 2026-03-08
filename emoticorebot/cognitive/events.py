from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import fabs
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


@dataclass
class CognitiveEvent:
    id: str
    timestamp: str
    session_id: str
    actor: str
    content: str
    eq: dict[str, Any] = field(default_factory=dict)
    iq: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def storage_path(cls, workspace: Path) -> Path:
        new_path = workspace / "memory" / "eq" / "events.jsonl"
        legacy_path = workspace / "data" / "memory" / "events.jsonl"
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if not new_path.exists() and legacy_path.exists():
            try:
                legacy_path.replace(new_path)
            except OSError:
                shutil.copy2(legacy_path, new_path)
        return new_path

    @classmethod
    def append(cls, workspace: Path, event: "CognitiveEvent") -> None:
        path = cls.storage_path(workspace)
        with path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    @classmethod
    def read_all(cls, workspace: Path) -> list[dict[str, Any]]:
        path = cls.storage_path(workspace)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
        return entries

    @classmethod
    def retrieve(
        cls,
        workspace: Path,
        query: str = "",
        *,
        actors: list[str] | None = None,
        k: int = 8,
    ) -> list[dict[str, Any]]:
        entries = cls.read_all(workspace)
        if actors:
            allowed = set(actors)
            entries = [entry for entry in entries if str(entry.get("actor", "")) in allowed]
        return cls._rank_entries(entries, query=query, limit=k)

    @classmethod
    def get_eq_context(cls, workspace: Path, query: str = "", *, k: int = 6) -> str:
        rows = cls.retrieve(workspace, query=query, actors=["user", "assistant"], k=max(k * 2, 8))
        if not rows:
            return ""

        lines: list[str] = []
        for row in rows:
            eq = row.get("eq") or {}
            if not isinstance(eq, dict):
                continue
            emotion = str(eq.get("emotion", "") or "").strip()
            if not emotion:
                continue
            intensity = float(eq.get("intensity", 0.0) or 0.0)
            relation = str(eq.get("relation", "") or "").strip()
            actor = str(row.get("actor", "event") or "event")
            summary = cls._normalize_text(str(row.get("content", "")), limit=120)
            if relation:
                lines.append(f"- [{actor}|{emotion}|{relation}|{intensity:.2f}] {summary}")
            else:
                lines.append(f"- [{actor}|{emotion}|{intensity:.2f}] {summary}")
            if len(lines) >= k:
                break

        if not lines:
            return ""
        return "## EQ 流\n" + "\n".join(lines)

    @classmethod
    def build_eq_sections(
        cls,
        workspace: Path,
        *,
        query: str = "",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
    ) -> list[str]:
        del current_emotion, pad_state
        eq_flow = cls.get_eq_context(workspace, query=query, k=4)
        if not eq_flow:
            return []
        return [cls._compact_section(eq_flow, 480)]

    @classmethod
    def build_turn_events(
        cls,
        *,
        state: dict,
        emotion_label: str,
        emotion_event,
        pad: dict[str, float],
        importance: float,
    ) -> list["CognitiveEvent"]:
        timestamp = datetime.now().isoformat()
        session_id = str(state.get("session_id", ""))
        user_input = str(state.get("user_input", "") or "").strip()
        output = str(state.get("output", "") or "").strip()
        iq = state.get("iq")

        events: list[CognitiveEvent] = []
        if user_input:
            user_emotion = cls._infer_user_emotion(user_input=user_input, emotion_event=emotion_event)
            user_eq = {
                "emotion": user_emotion["label"],
                "intensity": user_emotion["intensity"],
                "relation": cls._infer_relation(user_input=user_input, emotion_event=emotion_event),
                "importance": round(float(importance), 2),
            }
            events.append(
                cls(
                    id=f"evt_{uuid4().hex}",
                    timestamp=timestamp,
                    session_id=session_id,
                    actor="user",
                    content=user_input,
                    eq=user_eq,
                )
            )

        if output:
            eq = state.get("eq")
            assistant_relation = "谨慎" if cls._infer_relation(user_input=user_input, emotion_event=emotion_event) == "对抗" else "稳定"
            assistant_eq = {
                "emotion": emotion_label,
                "intensity": cls._assistant_intensity_from_pad(pad),
                "relation": assistant_relation,
                "importance": round(float(importance), 2),
            }
            events.append(
                cls(
                    id=f"evt_{uuid4().hex}",
                    timestamp=timestamp,
                    session_id=session_id,
                    actor="assistant",
                    content=output,
                    eq=assistant_eq,
                    iq={
                        "question": getattr(iq, "request", "") if iq is not None else "",
                        "confidence": float(getattr(iq, "confidence", 0.0) or 0.0) if iq is not None else 0.0,
                        "missing_params": list(getattr(iq, "missing_params", []) or []) if iq is not None else [],
                        "decision": str(getattr(eq, "final_decision", "") or "") if eq is not None else "",
                    },
                )
            )
        return events

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
    def _infer_user_emotion(*, user_input: str, emotion_event) -> dict[str, float | str]:
        text = (user_input or "").strip().lower()
        if not text:
            return {"label": "平静", "intensity": 0.3}
        behavior = str(getattr(emotion_event, "behavior", "") or "")
        if "开心" in behavior:
            return {"label": "开心", "intensity": 0.75}
        if "生气" in behavior:
            return {"label": "愤怒", "intensity": 0.82}
        if any(token in text for token in ["焦虑", "着急", "崩溃", "烦", "烦死了"]):
            return {"label": "焦虑", "intensity": 0.72}
        if any(token in text for token in ["我觉得", "设计", "架构", "方案", "记忆", "是不是", "要不要"]):
            return {"label": "专注", "intensity": 0.62}
        if any(token in text for token in ["？", "?", "不懂", "什么意思", "干嘛"]):
            return {"label": "困惑", "intensity": 0.58}
        return {"label": "平静", "intensity": 0.4}

    @staticmethod
    def _infer_relation(*, user_input: str, emotion_event) -> str:
        text = (user_input or "").strip().lower()
        behavior = str(getattr(emotion_event, "behavior", "") or "")
        if "生气" in behavior or any(token in text for token in ["滚", "闭嘴", "垃圾", "讨厌你", "烦死了"]):
            return "对抗"
        if "开心" in behavior or any(token in text for token in ["谢谢", "喜欢你", "爱你", "棒", "厉害"]):
            return "亲近"
        if any(token in text for token in ["我觉得", "设计", "架构", "方案", "一起", "先", "怎么做"]):
            return "合作"
        return "稳定"

    @staticmethod
    def _assistant_intensity_from_pad(pad: dict[str, float]) -> float:
        return round(
            max(
                fabs(float(pad.get("pleasure", 0.0) or 0.0)),
                fabs(float(pad.get("arousal", 0.0) or 0.0)),
                fabs(float(pad.get("dominance", 0.0) or 0.0)),
            ),
            2,
        )

    @staticmethod
    def _normalize_text(value: str, *, limit: int = 240) -> str:
        text = " ".join((value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _compact_section(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _token_overlap(query: str, values: Iterable[str]) -> float:
        query_tokens = set(query.lower().split())
        if not query_tokens:
            return 0.5
        haystack = " ".join(values).lower()
        hay_tokens = set(haystack.split())
        if not hay_tokens:
            return 0.0
        return min(len(query_tokens & hay_tokens) / max(len(query_tokens), 1), 1.0)

    @staticmethod
    def _recency_score(timestamp: str) -> float:
        if not timestamp:
            return 0.3
        try:
            age_hours = (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds() / 3600
        except Exception:
            return 0.3
        return max(0.05, min(1.0, 0.995 ** max(age_hours, 0)))

    @classmethod
    def _rank_entries(
        cls,
        entries: list[dict[str, Any]],
        *,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        ranked: list[tuple[float, dict[str, Any]]] = []
        for entry in entries:
            relevance = cls._token_overlap(query, [str(entry.get("content", ""))])
            recency = cls._recency_score(str(entry.get("timestamp", "") or ""))
            eq = entry.get("eq") or {}
            iq = entry.get("iq") or {}
            importance = float(eq.get("importance", 0.5) or 0.5) if isinstance(eq, dict) else 0.5
            confidence = float(iq.get("confidence", 1.0 if entry.get("actor") == "user" else 0.8) or 0.8) if isinstance(iq, dict) else 0.8
            score = 0.4 * relevance + 0.25 * recency + 0.2 * importance + 0.15 * confidence
            ranked.append((score, entry))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in ranked[:limit]]


__all__ = ["CognitiveEvent"]
