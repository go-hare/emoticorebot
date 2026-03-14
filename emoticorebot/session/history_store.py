"""Low-level JSONL persistence for thread histories."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

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


class HistoryStore:
    """Reads and writes a single JSONL history stream for each thread."""

    def __init__(self, root: Path, filename: str):
        self.root = ensure_dir(root)
        self.filename = filename

    @staticmethod
    def safe_thread_id(thread_id: str) -> str:
        return safe_filename(str(thread_id or "").replace(":", "_"))

    def thread_dir(self, thread_id: str) -> Path:
        return self.root / self.safe_thread_id(thread_id)

    def ensure_thread_dir(self, thread_id: str) -> Path:
        return ensure_dir(self.thread_dir(thread_id))

    def path_for(self, thread_id: str) -> Path:
        return self.thread_dir(thread_id) / self.filename

    def append_messages(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        self.ensure_thread_dir(thread_id)
        path = self.path_for(thread_id)
        with open(path, "a", encoding="utf-8") as file_obj:
            for message in messages:
                payload = normalize_message_payload(message)
                payload.setdefault("timestamp", datetime.now().isoformat())
                file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def clear_messages(self, thread_id: str) -> None:
        path = self.path_for(thread_id)
        if path.exists():
            path.write_text("", encoding="utf-8")

    def load_messages(self, thread_id: str, *, max_messages: int | None = None) -> list[dict[str, Any]]:
        path = self.path_for(thread_id)
        if not path.exists():
            return []
        try:
            messages = self.read_jsonl(path)
            if max_messages is not None:
                return messages[-max_messages:]
            return messages
        except Exception as exc:
            logger.warning("Failed to load {} for thread {}: {}", self.filename, thread_id, exc)
            return []

    def write_messages(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        self.ensure_thread_dir(thread_id)
        path = self.path_for(thread_id)
        with open(path, "w", encoding="utf-8") as file_obj:
            for message in messages:
                file_obj.write(json.dumps(normalize_message_payload(message), ensure_ascii=False) + "\n")

    def iter_thread_dirs(self):
        if not self.root.exists():
            return
        yield from (path for path in self.root.iterdir() if path.is_dir())

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if isinstance(data, dict):
                    messages.append(normalize_message_payload(data))
        return messages


__all__ = ["HistoryStore", "normalize_message_payload"]
