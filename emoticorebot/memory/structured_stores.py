from __future__ import annotations

from pathlib import Path

from emoticorebot.memory.jsonl_store import JsonlStore
from emoticorebot.memory.schema import EpisodicMemory, MemoryEvent, PlanMemory, ReflectiveMemory


class EventStore(JsonlStore):
    def __init__(self, workspace: Path):
        super().__init__(workspace / "data" / "memory" / "events.jsonl")

    def save(self, event: MemoryEvent) -> None:
        self.append(event.to_dict())

    def retrieve(
        self,
        query: str = "",
        *,
        kinds: list[str] | None = None,
        actors: list[str] | None = None,
        k: int = 8,
    ) -> list[dict]:
        entries = self.read_all()
        if kinds:
            allowed = set(kinds)
            entries = [entry for entry in entries if str(entry.get("kind", "")) in allowed]
        if actors:
            allowed = set(actors)
            entries = [entry for entry in entries if str(entry.get("actor", "")) in allowed]
        return self._rank_entries(entries, query=query, text_fields=("summary", "content"), limit=k)

    def get_context(self, query: str = "", *, k: int = 5) -> str:
        rows = self.retrieve(query=query, k=k)
        if not rows:
            return ""
        lines = []
        for row in rows:
            summary = self._normalize_text(str(row.get("summary", row.get("content", ""))), limit=180)
            kind = str(row.get("kind", "event"))
            lines.append(f"- [{kind}] {summary}")
        return "## Event Stream\n" + "\n".join(lines)


class EpisodicStore(JsonlStore):
    def __init__(self, workspace: Path):
        super().__init__(workspace / "data" / "memory" / "episodic.jsonl")

    def save(self, memory: EpisodicMemory) -> None:
        self.append(memory.to_dict())

    def retrieve(self, query: str = "", *, k: int = 5) -> list[dict]:
        return self._rank_entries(self.read_all(), query=query, text_fields=("summary",), limit=k)

    def get_context(self, query: str = "", *, k: int = 3) -> str:
        rows = self.retrieve(query=query, k=k)
        if not rows:
            return ""
        lines = []
        for row in rows:
            summary = self._normalize_text(str(row.get("summary", "")), limit=200)
            lines.append(f"- {summary}")
        return "## Episodic Memory\n" + "\n".join(lines)


class ReflectiveStore(JsonlStore):
    def __init__(self, workspace: Path):
        super().__init__(workspace / "data" / "memory" / "reflective.jsonl")

    def save(self, memory: ReflectiveMemory) -> None:
        self.append(memory.to_dict())

    def retrieve(self, query: str = "", *, themes: list[str] | None = None, k: int = 5) -> list[dict]:
        entries = self.read_all()
        if themes:
            allowed = set(themes)
            entries = [entry for entry in entries if str(entry.get("theme", "")) in allowed]
        return self._rank_entries(
            entries,
            query=query,
            text_fields=("insight", "theme"),
            limit=k,
            timestamp_field="created_at",
        )

    def get_context(self, query: str = "", *, k: int = 3) -> str:
        rows = self.retrieve(query=query, k=k)
        if not rows:
            return ""
        lines = []
        for row in rows:
            insight = self._normalize_text(str(row.get("insight", "")), limit=180)
            theme = str(row.get("theme", "reflection"))
            confidence = float(row.get("confidence", 0.0) or 0.0)
            lines.append(f"- [{theme}|{confidence:.2f}] {insight}")
        return "## Reflective Memory\n" + "\n".join(lines)


class PlanStore(JsonlStore):
    _ACTIVE = {"pending", "active", "blocked"}

    def __init__(self, workspace: Path):
        super().__init__(workspace / "data" / "memory" / "plans.jsonl")

    def save(self, plan: PlanMemory) -> None:
        self.append(plan.to_dict())

    def list_active(self, *, k: int = 8) -> list[dict]:
        latest: dict[str, dict] = {}
        for entry in self.read_all():
            plan_id = str(entry.get("id", "")).strip()
            if not plan_id:
                continue
            existing = latest.get(plan_id)
            if existing is None or str(entry.get("updated_at", "")) >= str(existing.get("updated_at", "")):
                latest[plan_id] = entry
        rows = [entry for entry in latest.values() if str(entry.get("status", "")) in self._ACTIVE]
        return self._rank_entries(
            rows,
            query="",
            text_fields=("title", "next_action"),
            limit=k,
            timestamp_field="updated_at",
        )

    def get_context(self, *, k: int = 5) -> str:
        rows = self.list_active(k=k)
        if not rows:
            return ""
        lines = []
        for row in rows:
            title = self._normalize_text(str(row.get("title", "")), limit=120)
            status = str(row.get("status", "pending"))
            next_action = self._normalize_text(str(row.get("next_action", "")), limit=100)
            if next_action:
                lines.append(f"- [{status}] {title} → {next_action}")
            else:
                lines.append(f"- [{status}] {title}")
        return "## Active Plans\n" + "\n".join(lines)


__all__ = ["EventStore", "EpisodicStore", "ReflectiveStore", "PlanStore"]
