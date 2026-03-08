from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from emoticorebot.memory.jsonl_store import JsonlStore, resolve_eq_memory_file


def _pad_cosine(
    p1: float,
    a1: float,
    d1: float,
    p2: float,
    a2: float,
    d2: float,
) -> float:
    dot = p1 * p2 + a1 * a2 + d1 * d2
    mag1 = math.sqrt(p1 * p1 + a1 * a1 + d1 * d1)
    mag2 = math.sqrt(p2 * p2 + a2 * a2 + d2 * d2)
    if mag1 < 1e-9 or mag2 < 1e-9:
        return 0.5
    cosine = dot / (mag1 * mag2)
    return (cosine + 1.0) / 2.0


class SemanticStore(JsonlStore):
    """Simple semantic memory store for factual notes."""

    def __init__(self, workspace: Path):
        super().__init__(resolve_eq_memory_file(workspace, "semantic.jsonl"))

    @property
    def available(self) -> bool:
        return True

    def save(
        self,
        text: str,
        tags: list[str] | None = None,
        importance: int | None = None,
        category: str | None = None,
        confidence: float | None = None,
        source_event_ids: list[str] | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
    ) -> None:
        if not text.strip():
            return
        self.append(
            {
                "timestamp": datetime.now().isoformat(),
                "text": text.strip(),
                "tags": tags or [],
                "importance": int(importance) if importance is not None else 5,
                "category": category or "other",
                "confidence": float(confidence) if confidence is not None else 0.7,
                "sourceEventIds": source_event_ids or [],
                "subject": subject or "",
                "predicate": predicate or "",
                "object": object_value or "",
            }
        )

    def retrieve(self, query: str, k: int = 5) -> list[str]:
        entries = self.read_all()
        query_l = query.lower().strip()
        if not query_l:
            return [str(entry.get("text", "")) for entry in entries[-k:]][::-1]

        scored: list[tuple[float, str]] = []
        for entry in entries:
            text = str(entry.get("text", ""))
            text_l = text.lower()
            score = 0.0
            if query_l in text_l:
                score += 1.0
            overlap = len(set(query_l.split()) & set(text_l.split()))
            score += overlap * 0.05
            if score > 0:
                scored.append((score, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [text for _, text in scored[:k]]

    def get_context(self, query: str = "", k: int = 5) -> str:
        rows = self.retrieve(query=query, k=k)
        if not rows:
            return ""
        return "## 语义记忆（Semantic）\n" + "\n".join(f"- {row}" for row in rows)


class RelationalStore(JsonlStore):
    """Relational memory store for user preference and warm interactions."""

    def __init__(self, workspace: Path):
        super().__init__(resolve_eq_memory_file(workspace, "relational.jsonl"))

    def save(
        self,
        text: str,
        emotion: str = "平静",
        importance: int = 5,
        confidence: float = 0.75,
        source_event_ids: list[str] | None = None,
        relation_type: str = "interaction",
        target: str = "user",
    ) -> None:
        if not text.strip():
            return
        self.append(
            {
                "timestamp": datetime.now().isoformat(),
                "text": text.strip(),
                "emotion": emotion,
                "importance": max(1, min(10, int(importance))),
                "confidence": max(0.0, min(1.0, float(confidence))),
                "sourceEventIds": source_event_ids or [],
                "relationType": relation_type,
                "target": target,
            }
        )

    def get_recent(self, limit: int = 20) -> list[dict]:
        return list(reversed(self.read_all()[-limit:]))

    def retrieve(self, query: str, current_emotion: str = "平静", k: int = 3) -> list[str]:
        entries = self.get_recent(limit=200)
        if not entries:
            return []
        query_l = query.lower().strip()
        scored: list[tuple[float, str]] = []
        for entry in entries:
            text = str(entry.get("text", ""))
            text_l = text.lower()
            score = float(entry.get("importance", 5)) / 10.0
            if query_l and query_l in text_l:
                score += 0.8
            if current_emotion and current_emotion == entry.get("emotion"):
                score += 0.5
            scored.append((score, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [text for _, text in scored[:k]]

    def get_context(self, query: str, current_emotion: str = "平静", k: int = 3) -> str:
        rows = self.retrieve(query=query, current_emotion=current_emotion, k=k)
        if not rows:
            return ""
        return "## 关系记忆（Relational）\n" + "\n".join(f"- {row}" for row in rows)


class AffectiveStore(JsonlStore):
    """Affective memory store for PAD-based emotional traces."""

    def __init__(self, workspace: Path):
        super().__init__(resolve_eq_memory_file(workspace, "affective.jsonl"))

    @property
    def available(self) -> bool:
        return True

    def save(
        self,
        description: str,
        pleasure: float,
        arousal: float,
        dominance: float,
        importance: float = 0.3,
        confidence: float = 0.8,
        source_event_ids: list[str] | None = None,
    ) -> None:
        if not description.strip():
            return
        self.append(
            {
                "timestamp": datetime.now().isoformat(),
                "description": description.strip(),
                "pleasure": float(pleasure),
                "arousal": float(arousal),
                "dominance": float(dominance),
                "importance": max(0.0, min(1.0, float(importance))),
                "confidence": max(0.0, min(1.0, float(confidence))),
                "sourceEventIds": source_event_ids or [],
            }
        )

    def retrieve(
        self,
        current_p: float,
        current_a: float,
        current_d: float,
        query: str = "",
        k: int = 5,
    ) -> list[str]:
        query_l = query.lower().strip()
        scored: list[tuple[float, str]] = []
        for item in self.read_all():
            description = str(item.get("description", ""))
            ts = str(item.get("timestamp", "") or "")
            try:
                hours = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                recency = 0.99 ** hours
            except Exception:
                recency = 0.5
            pad_sim = _pad_cosine(
                current_p,
                current_a,
                current_d,
                float(item.get("pleasure", 0.0)),
                float(item.get("arousal", 0.0)),
                float(item.get("dominance", 0.0)),
            )
            relevance = 0.6 if query_l and query_l in description.lower() else 0.0
            importance = float(item.get("importance", 0.3))
            score = 0.3 * recency + 0.3 * importance + 0.3 * pad_sim + 0.1 * relevance
            scored.append((score, description))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [description for _, description in scored[:k]]

    def get_context(
        self,
        current_p: float,
        current_a: float,
        current_d: float,
        query: str = "",
        k: int = 5,
    ) -> str:
        rows = self.retrieve(current_p, current_a, current_d, query=query, k=k)
        if not rows:
            return ""
        return "## 情绪轨迹记忆（Affective）\n" + "\n".join(f"- {row}" for row in rows)


__all__ = ["SemanticStore", "RelationalStore", "AffectiveStore"]
