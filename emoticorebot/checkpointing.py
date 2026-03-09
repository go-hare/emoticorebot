"""Checkpoint saver helpers for persistent LangGraph execution state."""

from __future__ import annotations

import pickle
from collections import defaultdict
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import PutOp
from langgraph.store.memory import InMemoryStore

from emoticorebot.utils.helpers import ensure_dir


class PersistentMemorySaver(InMemorySaver):
    """A lightweight file-backed wrapper around ``InMemorySaver``.

    The installed environment does not ship a SQLite/Postgres saver, so we persist the
    in-memory structures to a local pickle file after every mutation.
    """

    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        ensure_dir(self.path.parent)
        self._lock = RLock()
        super().__init__()
        self._load_from_disk()

    def put(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        with self._lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._persist_to_disk()
            return result

    def put_writes(self, config: Any, writes: Any, task_id: str, task_path: str = "") -> None:
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._persist_to_disk()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._persist_to_disk()

    def _load_from_disk(self) -> None:
        if not self.path.exists():
            return
        with self._lock:
            with open(self.path, "rb") as handle:
                payload = pickle.load(handle)

            restored_storage: defaultdict[str, Any] = defaultdict(lambda: defaultdict(dict))
            for thread_id, namespace_map in dict(payload.get("storage", {}) or {}).items():
                restored_storage[thread_id] = defaultdict(dict)
                for checkpoint_ns, checkpoint_map in dict(namespace_map or {}).items():
                    restored_storage[thread_id][checkpoint_ns] = dict(checkpoint_map or {})

            restored_writes: defaultdict[tuple[str, str, str], dict[Any, Any]] = defaultdict(dict)
            for key, value in dict(payload.get("writes", {}) or {}).items():
                restored_writes[key] = dict(value or {})

            self.storage = restored_storage
            self.writes = restored_writes
            self.blobs = dict(payload.get("blobs", {}) or {})

    def _persist_to_disk(self) -> None:
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "storage": {
                thread_id: {checkpoint_ns: dict(checkpoint_map or {}) for checkpoint_ns, checkpoint_map in dict(namespace_map or {}).items()}
                for thread_id, namespace_map in dict(self.storage or {}).items()
            },
            "writes": {key: dict(value or {}) for key, value in dict(self.writes or {}).items()},
            "blobs": dict(self.blobs or {}),
        }
        with open(temp, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        temp.replace(self.path)


class PersistentMemoryStore(InMemoryStore):
    """A lightweight file-backed wrapper around ``InMemoryStore``."""

    def __init__(self, path: Path, *, index: Any | None = None):
        self.path = Path(path).expanduser().resolve()
        ensure_dir(self.path.parent)
        self._lock = RLock()
        super().__init__(index=index)
        self._load_from_disk()

    def batch(self, ops: Iterable[Any]) -> list[Any]:
        op_list = list(ops)
        result = super().batch(op_list)
        if any(isinstance(op, PutOp) for op in op_list):
            self._persist_to_disk()
        return result

    async def abatch(self, ops: Iterable[Any]) -> list[Any]:
        op_list = list(ops)
        result = await super().abatch(op_list)
        if any(isinstance(op, PutOp) for op in op_list):
            self._persist_to_disk()
        return result

    def _load_from_disk(self) -> None:
        if not self.path.exists():
            return
        with self._lock:
            with open(self.path, "rb") as handle:
                payload = pickle.load(handle)

            restored_data: defaultdict[tuple[str, ...], dict[str, Any]] = defaultdict(dict)
            for namespace, items in dict(payload.get("data", {}) or {}).items():
                restored_data[tuple(namespace)] = dict(items or {})

            restored_vectors: defaultdict[tuple[str, ...], dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(dict))
            for namespace, key_map in dict(payload.get("vectors", {}) or {}).items():
                namespace_key = tuple(namespace)
                restored_vectors[namespace_key] = defaultdict(dict)
                for key, path_map in dict(key_map or {}).items():
                    restored_vectors[namespace_key][str(key)] = dict(path_map or {})

            self._data = restored_data
            self._vectors = restored_vectors

    def _persist_to_disk(self) -> None:
        with self._lock:
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = {
                "data": {
                    tuple(namespace): dict(items or {})
                    for namespace, items in dict(self._data or {}).items()
                },
                "vectors": {
                    tuple(namespace): {
                        str(key): dict(path_map or {})
                        for key, path_map in dict(key_map or {}).items()
                    }
                    for namespace, key_map in dict(self._vectors or {}).items()
                },
            }
            with open(temp, "wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temp.replace(self.path)


__all__ = ["PersistentMemorySaver", "PersistentMemoryStore"]
