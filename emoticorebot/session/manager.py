"""Session management for conversation history."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from emoticorebot.utils.task_context import build_task_context
from emoticorebot.utils.helpers import ensure_dir, safe_filename
from emoticorebot.utils.llm_utils import normalize_content_blocks


def _new_message_id() -> str:
    return f"msg_{uuid4().hex[:16]}"


def _normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        call_id = str(item.get("id", "") or "").strip()
        name = str(item.get("name", "") or "").strip()
        args = item.get("args", {})
        if not call_id and not name:
            continue
        payload: dict[str, Any] = {}
        if call_id:
            payload["id"] = call_id
        if name:
            payload["name"] = name
        if isinstance(args, dict):
            payload["args"] = args
        elif isinstance(item.get("function"), dict):
            function = item.get("function") or {}
            payload["name"] = str(function.get("name", "") or payload.get("name", "")).strip()
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    payload["args"] = json.loads(arguments)
                except Exception:
                    payload["args"] = {"raw": arguments}
        calls.append(payload)
    return calls


def normalize_message_payload(message: dict[str, Any], *, default_message_id: str | None = None) -> dict[str, Any]:
    message_id = str(message.get("message_id", "") or default_message_id or _new_message_id()).strip()
    role = str(message.get("role", "user") or "user").strip() or "user"
    payload: dict[str, Any] = {
        "message_id": message_id,
        "role": role,
        "content": normalize_content_blocks(message.get("content", [])),
    }

    timestamp = str(message.get("timestamp", "") or "").strip()
    if timestamp:
        payload["timestamp"] = timestamp

    model_name = str(message.get("model_name", "") or "").strip()
    if model_name:
        payload["model_name"] = model_name

    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = message.get(key)
        if value not in (None, ""):
            try:
                payload[key] = int(value)
            except Exception:
                pass

    tool_calls = _normalize_tool_calls(message.get("tool_calls"))
    if tool_calls:
        payload["tool_calls"] = tool_calls

    tool_call_id = str(message.get("tool_call_id", "") or "").strip()
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id

    task = message.get("task")
    if isinstance(task, dict):
        payload["task"] = task

    for key in ("phase", "event", "source", "node"):
        value = str(message.get(key, "") or "").strip()
        if value:
            payload[key] = value

    namespace = message.get("namespace")
    if isinstance(namespace, list) and namespace:
        payload["namespace"] = [str(item).strip() for item in namespace if str(item).strip()]

    brain = message.get("brain")
    if isinstance(brain, dict) and brain:
        payload["brain"] = brain

    meta = message.get("meta")
    if isinstance(meta, dict) and meta:
        payload["meta"] = meta

    return payload


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    Background consolidation extracts durable memories into the structured
    memory stores but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: Any, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = normalize_message_payload(
            {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
            }
        )
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 500,
        *,
        include_task_context: bool = True,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a user turn."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        out: list[dict[str, Any]] = []
        for message in sliced:
            content = normalize_content_blocks(message.get("content", []))
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
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """Manage persistent dialogue and internal histories."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self._cache: dict[str, Session] = {}

    @staticmethod
    def _safe_key(key: str) -> str:
        return safe_filename(key.replace(":", "_"))

    def _get_session_dir(self, key: str) -> Path:
        return self.sessions_dir / self._safe_key(key)

    def _ensure_session_dir(self, key: str) -> Path:
        return ensure_dir(self._get_session_dir(key))

    def _get_dialogue_path(self, key: str) -> Path:
        return self._get_session_dir(key) / "dialogue.jsonl"

    def _get_internal_path(self, key: str) -> Path:
        return self._get_session_dir(key) / "internal.jsonl"

    def _get_session_path(self, key: str) -> Path:
        return self._get_dialogue_path(key)

    def append_internal_messages(self, key: str, messages: list[dict[str, Any]]) -> None:
        """Append internal deliberation messages for the current turn."""
        if not messages:
            return
        self._ensure_session_dir(key)
        path = self._get_internal_path(key)
        with open(path, "a", encoding="utf-8") as f:
            for message in messages:
                payload = normalize_message_payload(message)
                payload.setdefault("timestamp", datetime.now().isoformat())
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def clear_internal_messages(self, key: str) -> None:
        """Clear persisted internal deliberation history for `/new`."""
        path = self._get_internal_path(key)
        if path.exists():
            path.write_text("", encoding="utf-8")

    def get_internal_messages(
        self,
        key: str,
        *,
        max_messages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Load persisted internal deliberation messages."""
        path = self._get_internal_path(key)
        if not path.exists():
            return []
        try:
            messages: list[dict[str, Any]] = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    messages.append(normalize_message_payload(data))
            if max_messages is not None:
                return messages[-max_messages:]
            return messages
        except Exception as e:
            logger.warning("Failed to load internal messages for session {}: {}", key, e)
            return []

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def get(self, key: str) -> Session | None:
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is not None:
            self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        dialogue_path = self._get_dialogue_path(key)

        if dialogue_path.exists():
            return self._load_directory_session(key, dialogue_path=dialogue_path)

        return None

    def _load_directory_session(self, key: str, *, dialogue_path: Path) -> Session | None:
        try:
            messages = self._read_jsonl_messages(dialogue_path)
            return self._session_from_payload(
                key=key,
                messages=messages,
                metadata={},
                created_at=self._infer_created_at(messages) or datetime.now(),
                updated_at=self._infer_updated_at(messages) or datetime.now(),
                last_consolidated=0,
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def _session_from_payload(
        self,
        *,
        key: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        created_at: datetime,
        updated_at: datetime,
        last_consolidated: int,
    ) -> Session:
        return Session(
            key=key,
            messages=messages,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
            last_consolidated=max(0, min(last_consolidated, len(messages))),
        )

    @staticmethod
    def _read_jsonl_messages(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if isinstance(data, dict):
                    messages.append(normalize_message_payload(data))
        return messages

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def save(self, session: Session) -> None:
        session_dir = self._ensure_session_dir(session.key)
        dialogue_path = session_dir / "dialogue.jsonl"

        session.updated_at = datetime.now()

        with open(dialogue_path, "w", encoding="utf-8") as f:
            for message in session.messages:
                f.write(json.dumps(normalize_message_payload(message), ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions_by_key: dict[str, dict[str, Any]] = {}

        for path in self.sessions_dir.iterdir():
            if not path.is_dir():
                continue
            dialogue_path = path / "dialogue.jsonl"
            messages = self._read_jsonl_messages(dialogue_path)
            key = path.name
            created_at = self._infer_created_at(messages)
            updated_at = self._infer_updated_at(messages)
            sessions_by_key[key] = {
                "key": key,
                "created_at": created_at.isoformat() if created_at else "",
                "updated_at": updated_at.isoformat() if updated_at else "",
                "path": str(dialogue_path),
            }

        return sorted(sessions_by_key.values(), key=lambda item: item.get("updated_at", ""), reverse=True)

    @staticmethod
    def _infer_created_at(messages: list[dict[str, Any]]) -> datetime | None:
        for message in messages:
            created_at = SessionManager._parse_datetime(message.get("timestamp"))
            if created_at is not None:
                return created_at
        return None

    @staticmethod
    def _infer_updated_at(messages: list[dict[str, Any]]) -> datetime | None:
        for message in reversed(messages):
            updated_at = SessionManager._parse_datetime(message.get("timestamp"))
            if updated_at is not None:
                return updated_at
        return None
