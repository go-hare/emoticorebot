"""Session management for conversation history."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.session.iq_context import build_iq_context
from emoticorebot.utils.helpers import ensure_dir, safe_filename


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

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 500,
        *,
        include_iq_context: bool = True,
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
            content = message.get("content", "")
            if include_iq_context and message.get("role") == "assistant":
                iq_context = build_iq_context(message)
                if iq_context:
                    content = f"{content}\n\n{iq_context}"

            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name"):
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
    """Manage persistent `user_eq` and `eq_iq` histories."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path.home() / ".emoticorebot" / "sessions"
        self._cache: dict[str, Session] = {}

    @staticmethod
    def _safe_key(key: str) -> str:
        return safe_filename(key.replace(":", "_"))

    def _get_session_dir(self, key: str) -> Path:
        return self.sessions_dir / self._safe_key(key)

    def _ensure_session_dir(self, key: str) -> Path:
        return ensure_dir(self._get_session_dir(key))

    def _get_user_eq_path(self, key: str) -> Path:
        return self._get_session_dir(key) / "user_eq.jsonl"

    def _get_eq_iq_path(self, key: str) -> Path:
        return self._get_session_dir(key) / "eq_iq.jsonl"

    def _get_meta_path(self, key: str) -> Path:
        return self._get_session_dir(key) / "meta.json"

    def _get_session_path(self, key: str) -> Path:
        return self._get_user_eq_path(key)

    def _get_flat_session_path(self, key: str) -> Path:
        return self.sessions_dir / f"{self._safe_key(key)}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        return self.legacy_sessions_dir / f"{self._safe_key(key)}.jsonl"

    def append_eq_iq_messages(self, key: str, messages: list[dict[str, Any]]) -> None:
        """Append internal EQ↔IQ messages for the current turn."""
        if not messages:
            return
        self._ensure_session_dir(key)
        path = self._get_eq_iq_path(key)
        with open(path, "a", encoding="utf-8") as f:
            for message in messages:
                payload = dict(message)
                payload.setdefault("timestamp", datetime.now().isoformat())
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def clear_eq_iq_messages(self, key: str) -> None:
        """Clear persisted internal EQ↔IQ history for `/new`."""
        path = self._get_eq_iq_path(key)
        if path.exists():
            path.write_text("", encoding="utf-8")

    def get_eq_iq_messages(
        self,
        key: str,
        *,
        max_messages: int | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load persisted internal EQ↔IQ messages, optionally filtered by task."""
        path = self._get_eq_iq_path(key)
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
                    if task_id and str(data.get("task_id", "") or "") != task_id:
                        continue
                    messages.append(data)
            if max_messages is not None:
                return messages[-max_messages:]
            return messages
        except Exception as e:
            logger.warning("Failed to load eq_iq messages for session {}: {}", key, e)
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
        meta_path = self._get_meta_path(key)
        user_eq_path = self._get_user_eq_path(key)

        if meta_path.exists() or user_eq_path.exists():
            return self._load_directory_session(key, meta_path=meta_path, user_eq_path=user_eq_path)

        flat_candidates = [self._get_flat_session_path(key), self._get_legacy_session_path(key)]
        for legacy_path in flat_candidates:
            if not legacy_path.exists():
                continue
            payload = self._read_flat_session_payload(legacy_path, fallback_key=key)
            if payload is None:
                continue
            payload.pop("loaded_key", None)
            session = self._session_from_payload(key=key, **payload)
            self.save(session)
            logger.info("Migrated session {} from legacy flat file {}", key, legacy_path)
            return session

        return None

    def _load_directory_session(self, key: str, *, meta_path: Path, user_eq_path: Path) -> Session | None:
        try:
            meta = self._read_meta(meta_path)
            messages = self._read_jsonl_messages(user_eq_path)
            return self._session_from_payload(
                key=key,
                messages=messages,
                metadata=meta.get("metadata", {}),
                created_at=self._parse_datetime(meta.get("created_at")) or self._infer_created_at(messages) or datetime.now(),
                updated_at=self._parse_datetime(meta.get("updated_at")) or self._infer_updated_at(messages) or datetime.now(),
                last_consolidated=int(meta.get("last_consolidated", 0) or 0),
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

    def _read_flat_session_payload(self, path: Path, *, fallback_key: str) -> dict[str, Any] | None:
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            loaded_key = fallback_key
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    if data.get("_type") == "metadata":
                        loaded_key = str(data.get("key", "") or "").strip() or fallback_key
                        metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
                        created_at = self._parse_datetime(data.get("created_at"))
                        updated_at = self._parse_datetime(data.get("updated_at"))
                        last_consolidated = int(data.get("last_consolidated", 0) or 0)
                        continue
                    messages.append(data)

            return {
                "messages": messages,
                "metadata": metadata,
                "created_at": created_at or self._infer_created_at(messages) or datetime.now(),
                "updated_at": updated_at or self._infer_updated_at(messages) or datetime.now(),
                "last_consolidated": last_consolidated,
                "loaded_key": loaded_key,
            }
        except Exception as e:
            logger.warning("Failed to read legacy session {}: {}", path, e)
            return None

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
                    messages.append(data)
        return messages

    @staticmethod
    def _read_meta(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

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
        meta_path = session_dir / "meta.json"
        user_eq_path = session_dir / "user_eq.jsonl"

        session.updated_at = datetime.now()

        meta_payload = {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
        }
        meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with open(user_eq_path, "w", encoding="utf-8") as f:
            for message in session.messages:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions_by_key: dict[str, dict[str, Any]] = {}

        for path in self.sessions_dir.iterdir():
            if not path.is_dir():
                continue
            meta = self._read_meta(path / "meta.json")
            key = str(meta.get("key", "") or "").strip() or path.name
            updated_at = str(meta.get("updated_at", "") or "")
            created_at = str(meta.get("created_at", "") or "")
            sessions_by_key[key] = {
                "key": key,
                "created_at": created_at,
                "updated_at": updated_at,
                "path": str(path / "user_eq.jsonl"),
            }

        for legacy_path in list(self.sessions_dir.glob("*.jsonl")) + list(self.legacy_sessions_dir.glob("*.jsonl")):
            payload = self._read_flat_session_payload(legacy_path, fallback_key=legacy_path.stem)
            if payload is None:
                continue
            key = str(payload.get("loaded_key", "") or legacy_path.stem)
            if key in sessions_by_key:
                continue
            created_at = payload["created_at"].isoformat() if isinstance(payload.get("created_at"), datetime) else ""
            updated_at = payload["updated_at"].isoformat() if isinstance(payload.get("updated_at"), datetime) else ""
            sessions_by_key[key] = {
                "key": key,
                "created_at": created_at,
                "updated_at": updated_at,
                "path": str(legacy_path),
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
