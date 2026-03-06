from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class RelationalStore:
    """Relational memory store for user preference and warm interactions."""

    def __init__(self, workspace: Path):
        self._file = workspace / "data" / "relational_memories.jsonl"
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def save(self, text: str, emotion: str = "平静", importance: int = 5) -> None:
        if not text.strip():
            return
        entry = {
            "timestamp": datetime.now().isoformat(),
            "text": text.strip(),
            "emotion": emotion,
            "importance": max(1, min(10, int(importance))),
        }
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_recent(self, limit: int = 20) -> list[dict]:
        if not self._file.exists():
            return []
        entries: list[dict] = []
        with self._file.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        return list(reversed(entries[-limit:]))

    def retrieve(self, query: str, current_emotion: str = "平静", k: int = 3) -> list[str]:
        entries = self.get_recent(limit=200)
        if not entries:
            return []
        query_l = query.lower().strip()
        scored: list[tuple[float, str]] = []
        for e in entries:
            text = str(e.get("text", ""))
            text_l = text.lower()
            score = float(e.get("importance", 5)) / 10.0
            if query_l and query_l in text_l:
                score += 0.8
            if current_emotion and current_emotion == e.get("emotion"):
                score += 0.5
            scored.append((score, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [text for _, text in scored[:k]]

    def get_context(self, query: str, current_emotion: str = "平静", k: int = 3) -> str:
        rows = self.retrieve(query=query, current_emotion=current_emotion, k=k)
        if not rows:
            return ""
        return "## 关系记忆（Relational）\n" + "\n".join(f"- {r}" for r in rows)

