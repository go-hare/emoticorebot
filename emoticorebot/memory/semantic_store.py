from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class SemanticStore:
    """Simple semantic memory store for factual notes."""

    def __init__(self, workspace: Path):
        self._file = workspace / "data" / "semantic_memories.jsonl"
        self._file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def available(self) -> bool:
        return True

    def count(self) -> int:
        if not self._file.exists():
            return 0
        return sum(1 for _ in self._file.open("r", encoding="utf-8"))

    def save(
        self,
        text: str,
        tags: list[str] | None = None,
        importance: int | None = None,
        category: str | None = None,
    ) -> None:
        if not text.strip():
            return
        entry = {
            "timestamp": datetime.now().isoformat(),
            "text": text.strip(),
            "tags": tags or [],
            "importance": int(importance) if importance is not None else 5,
            "category": category or "other",
        }
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def retrieve(self, query: str, k: int = 5) -> list[str]:
        if not self._file.exists():
            return []
        query_l = query.lower().strip()
        entries: list[dict] = []
        with self._file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        if not query_l:
            return [e.get("text", "") for e in entries[-k:]][::-1]

        scored: list[tuple[float, str]] = []
        for e in entries:
            text = str(e.get("text", ""))
            text_l = text.lower()
            score = 0.0
            if query_l in text_l:
                score += 1.0
            overlap = len(set(query_l.split()) & set(text_l.split()))
            score += overlap * 0.05
            if score > 0:
                scored.append((score, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [text for _, text in scored[:k]]

    def get_context(self, query: str = "", k: int = 5) -> str:
        rows = self.retrieve(query=query, k=k)
        if not rows:
            return ""
        return "## 语义记忆（Semantic）\n" + "\n".join(f"- {r}" for r in rows)

