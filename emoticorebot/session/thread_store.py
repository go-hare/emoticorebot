"""Thread persistence layer for raw left/right session histories."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.utils.llm_utils import normalize_content_blocks
from emoticorebot.utils.task_context import build_task_context

from emoticorebot.session.history_store import HistoryStore, normalize_message_payload


@dataclass
class ConversationThread:
    """Persistent conversation thread used as the history source of truth."""

    thread_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0

    def add_message(self, role: str, content: Any, **kwargs: Any) -> None:
        message = normalize_message_payload(
            {
                "role": role,
                "content": content,
                "created_at": datetime.now().isoformat(),
                **kwargs,
            }
        )
        self.messages.append(message)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 500,
        *,
        include_task_context: bool = True,
    ) -> list[dict[str, Any]]:
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        for index, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[index:]
                break

        out: list[dict[str, Any]] = []
        for message in sliced:
            content = normalize_content_blocks(message.get("content_blocks", []))
            if include_task_context and message.get("role") == "assistant":
                task_context = build_task_context(message)
                if task_context:
                    content = [*content, {"type": "text", "text": task_context}]

            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class ThreadStore:
    """Owns cached conversation threads and their on-disk histories."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.threads_dir = workspace / "session"
        self.left_store = HistoryStore(self.threads_dir, "left.jsonl")
        self.right_store = HistoryStore(self.threads_dir, "right.jsonl")
        self._cache: dict[str, ConversationThread] = {}

    def append_right_messages(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        self.right_store.append_messages(thread_id, messages)

    def clear_right_messages(self, thread_id: str) -> None:
        self.right_store.clear_messages(thread_id)

    def get_right_messages(
        self,
        thread_id: str,
        *,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.right_store.load_messages(thread_id, max_messages=max_messages)

    def get_or_create(self, thread_id: str) -> ConversationThread:
        if thread_id in self._cache:
            return self._cache[thread_id]

        thread = self._load(thread_id)
        if thread is None:
            thread = ConversationThread(thread_id=thread_id)

        self._cache[thread_id] = thread
        return thread

    def get(self, thread_id: str) -> ConversationThread | None:
        if thread_id in self._cache:
            return self._cache[thread_id]

        thread = self._load(thread_id)
        if thread is not None:
            self._cache[thread_id] = thread
        return thread

    def save(self, thread: ConversationThread) -> None:
        thread.updated_at = datetime.now()
        self.left_store.write_messages(thread.thread_id, thread.messages)
        self._cache[thread.thread_id] = thread

    def invalidate(self, thread_id: str) -> None:
        self._cache.pop(thread_id, None)

    def list_threads(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self.left_store.iter_thread_dirs():
            left_path = path / "left.jsonl"
            messages = HistoryStore.read_jsonl(left_path)
            created_at = self._infer_created_at(messages)
            updated_at = self._infer_updated_at(messages)
            records.append(
                {
                    "thread_id": path.name,
                    "created_at": created_at.isoformat() if created_at else "",
                    "updated_at": updated_at.isoformat() if updated_at else "",
                    "path": str(left_path),
                }
            )
        return sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True)

    def _load(self, thread_id: str) -> ConversationThread | None:
        left_path = self.left_store.path_for(thread_id)
        if not left_path.exists():
            return None
        try:
            messages = HistoryStore.read_jsonl(left_path)
            return self._thread_from_payload(
                thread_id=thread_id,
                messages=messages,
                metadata={},
                created_at=self._infer_created_at(messages) or datetime.now(),
                updated_at=self._infer_updated_at(messages) or datetime.now(),
                last_consolidated=0,
            )
        except Exception as exc:
            logger.warning("Failed to load thread {}: {}", thread_id, exc)
            return None

    @staticmethod
    def _thread_from_payload(
        *,
        thread_id: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        created_at: datetime,
        updated_at: datetime,
        last_consolidated: int,
    ) -> ConversationThread:
        return ConversationThread(
            thread_id=thread_id,
            messages=messages,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
            last_consolidated=max(0, min(last_consolidated, len(messages))),
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    @staticmethod
    def _infer_created_at(messages: list[dict[str, Any]]) -> datetime | None:
        for message in messages:
            created_at = ThreadStore._parse_datetime(message.get("created_at"))
            if created_at is not None:
                return created_at
        return None

    @staticmethod
    def _infer_updated_at(messages: list[dict[str, Any]]) -> datetime | None:
        for message in reversed(messages):
            updated_at = ThreadStore._parse_datetime(message.get("created_at"))
            if updated_at is not None:
                return updated_at
        return None


__all__ = ["ConversationThread", "ThreadStore"]
