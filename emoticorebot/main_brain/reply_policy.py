"""User-visible wording helpers for the main-brain runtime."""

from __future__ import annotations

from emoticorebot.execution.store import ExecutionRecord


class ReplyPolicy:
    """Formats concise user-visible replies from execution state and events."""

    @staticmethod
    def execution_accepted(task: ExecutionRecord | None, *, reason: str | None = None) -> str:
        prefix = task.title if task is not None else "这个请求"
        if reason:
            return f"{prefix} 已开始处理。{reason}".strip()
        return f"{prefix} 已开始处理。".strip()

    @staticmethod
    def execution_progress(
        task: ExecutionRecord | None,
        *,
        summary: str,
        next_step: str | None = None,
    ) -> str:
        prefix = task.title if task is not None else "这个请求"
        if next_step:
            return f"{prefix} 进展：{summary}。下一步：{next_step}".strip()
        return f"{prefix} 进展：{summary}".strip()

    @staticmethod
    def execution_rejected(task: ExecutionRecord | None, *, reason: str) -> str:
        prefix = task.title if task is not None else "这个请求"
        return f"{prefix} 这次先不继续执行。{reason or '当前无法处理。'}".strip()

    @staticmethod
    def execution_result(
        task: ExecutionRecord | None,
        *,
        decision: str,
        summary: str | None,
        result_text: str | None,
        outcome: str | None = None,
    ) -> str:
        if decision == "answer_only":
            return str(result_text or summary or "我先给你一个直接判断。").strip()
        prefix = task.title if task is not None else "这个请求"
        if outcome == "cancelled":
            return f"{prefix} 已取消。{result_text or summary or '任务已取消。'}".strip()
        if outcome == "failed":
            return f"{prefix} 失败了。{result_text or summary or '执行失败。'}".strip()
        return f"{prefix} 已完成。{result_text or summary or '已经处理好了。'}".strip()


__all__ = ["ReplyPolicy"]
