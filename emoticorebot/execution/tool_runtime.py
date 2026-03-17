"""Bridge from executor internals back to a progress-reporting runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ProgressReporter = Callable[[str], Awaitable[None]]
DetailedProgressReporter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class ExecutionToolRuntime:
    """Holds the currently executing progress reporter for worker tools."""

    reporter: DetailedProgressReporter | None = None

    def bind_reporter(self, reporter: DetailedProgressReporter | None) -> None:
        self.reporter = reporter

    def clear(self) -> None:
        self.reporter = None

    async def report_progress(self, message: str, **payload: Any) -> str:
        text = str(message or "").strip()
        if not text:
            return "汇报内容为空"
        if self.reporter is None:
            return "当前无法汇报（未在执行中）"
        await self.reporter(text, dict(payload))
        return f"已汇报: {text}"


__all__ = ["DetailedProgressReporter", "ExecutionToolRuntime", "ProgressReporter"]
