"""Bridge from executor internals back to a progress-reporting runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from emoticorebot.runtime.running_task import RunningTask, TaskRuntime

ProgressReporter = Callable[[str], Awaitable[None]]
DetailedProgressReporter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class ExecutionToolRuntime:
    """Holds the currently executing task/runtime pair for progress callbacks."""

    runtime: "TaskRuntime | None" = None
    task: "RunningTask | None" = None
    reporter: DetailedProgressReporter | None = None

    def bind(self, *, runtime: "TaskRuntime", task: "RunningTask") -> None:
        self.runtime = runtime
        self.task = task
        self.reporter = None

    def bind_reporter(self, reporter: DetailedProgressReporter | None) -> None:
        self.runtime = None
        self.task = None
        self.reporter = reporter

    def clear(self) -> None:
        self.runtime = None
        self.task = None
        self.reporter = None

    async def report_progress(self, message: str, **payload: Any) -> str:
        if self.reporter is not None:
            text = str(message or "").strip()
            if not text:
                return "汇报内容为空"
            await self.reporter(text, dict(payload))
            return f"已汇报: {text}"
        if self.runtime is None or self.task is None:
            return "当前无法汇报（未在执行中）"
        text = str(message or "").strip()
        if not text:
            return "汇报内容为空"
        await self.runtime.report_progress(self.task, text, **payload)
        return f"已汇报: {text}"


__all__ = ["DetailedProgressReporter", "ExecutionToolRuntime", "ProgressReporter"]
