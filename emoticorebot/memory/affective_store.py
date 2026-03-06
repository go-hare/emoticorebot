from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path


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


class AffectiveStore:
    """Affective memory store for PAD-based emotional traces."""

    def __init__(self, workspace: Path):
        self._file = workspace / "data" / "affective_memories.jsonl"
        self._file.parent.mkdir(parents=True, exist_ok=True)

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
    ) -> None:
        if not description.strip():
            return
        entry = {
            "timestamp": datetime.now().isoformat(),
            "description": description.strip(),
            "pleasure": float(pleasure),
            "arousal": float(arousal),
            "dominance": float(dominance),
            "importance": max(0.0, min(1.0, float(importance))),
        }
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def retrieve(
        self,
        current_p: float,
        current_a: float,
        current_d: float,
        query: str = "",
        k: int = 5,
    ) -> list[str]:
        if not self._file.exists():
            return []
        query_l = query.lower().strip()
        now = datetime.now()
        scored: list[tuple[float, str]] = []
        with self._file.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                description = str(item.get("description", ""))
                ts = item.get("timestamp", "")
                try:
                    hours = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
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
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:k]]

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
        return "## 情绪轨迹记忆（Affective）\n" + "\n".join(f"- {r}" for r in rows)

