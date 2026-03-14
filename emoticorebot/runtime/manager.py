"""Manager for session-scoped live runtimes."""

from __future__ import annotations

from typing import Callable

from emoticorebot.runtime.session_runtime import SessionRuntime

RuntimeFactory = Callable[[str], SessionRuntime]
RuntimeCreatedCallback = Callable[[str, SessionRuntime], None]


class RuntimeManager:
    """Owns the mapping from session id to SessionRuntime."""

    def __init__(self, runtime_factory: RuntimeFactory):
        self._runtime_factory = runtime_factory
        self._runtimes: dict[str, SessionRuntime] = {}
        self._on_runtime_created: RuntimeCreatedCallback | None = None

    def set_on_runtime_created(self, callback: RuntimeCreatedCallback | None) -> None:
        self._on_runtime_created = callback

    def get(self, session_id: str) -> SessionRuntime | None:
        key = self._normalize_session_id(session_id)
        return self._runtimes.get(key)

    def get_or_create_runtime(self, session_id: str) -> SessionRuntime:
        key = self._normalize_session_id(session_id)
        runtime = self._runtimes.get(key)
        if runtime is not None:
            return runtime

        runtime = self._runtime_factory(key)
        self._runtimes[key] = runtime
        if self._on_runtime_created is not None:
            self._on_runtime_created(key, runtime)
        return runtime

    def remove(self, session_id: str) -> SessionRuntime | None:
        key = self._normalize_session_id(session_id)
        return self._runtimes.pop(key, None)

    def session_ids(self) -> list[str]:
        return list(self._runtimes.keys())

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        return str(session_id or "__default__").strip() or "__default__"


__all__ = ["RuntimeManager"]
