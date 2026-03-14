"""Bridge from executor internals back to the session runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emoticorebot.runtime.running_task import RunningTask
    from emoticorebot.runtime.session_runtime import SessionRuntime


@dataclass
class ExecutionToolRuntime:
    """Holds the currently executing task/runtime pair for progress callbacks."""

    runtime: "SessionRuntime | None" = None
    task: "RunningTask | None" = None

    def bind(self, *, runtime: "SessionRuntime", task: "RunningTask") -> None:
        self.runtime = runtime
        self.task = task

    def clear(self) -> None:
        self.runtime = None
        self.task = None

    async def report_progress(self, message: str, **payload: Any) -> str:
        if self.runtime is None or self.task is None:
            return "当前无法汇报（未在执行中）"
        text = str(message or "").strip()
        if not text:
            return "汇报内容为空"
        await self.runtime.report_progress(self.task, text, **payload)
        return f"已汇报: {text}"


__all__ = ["ExecutionToolRuntime"]
