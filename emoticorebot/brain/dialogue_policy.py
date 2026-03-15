"""User-visible wording helpers for the executive brain."""

from __future__ import annotations

import re

from emoticorebot.protocol.events import (
    ReplyBlockedPayload,
    TaskCancelledEventPayload,
    TaskFailedEventPayload,
    TaskNeedInputEventPayload,
    TaskProgressEventPayload,
    TaskResultEventPayload,
)
from emoticorebot.protocol.task_models import ContentBlock
from emoticorebot.runtime.task_store import RuntimeTaskRecord


class DialoguePolicy:
    """Formats concise user-visible replies from runtime state and events."""

    _GREETING_PATTERN = re.compile(
        r"^(你好|您好|嗨|hi|hello|哈喽|在吗|在不在|早上好|中午好|晚上好)[!！。\.~～\s]*$",
        re.IGNORECASE,
    )

    def direct_reply(self, user_input: str, active_task: RuntimeTaskRecord | None) -> str:
        text = str(user_input or "").strip()
        if self._GREETING_PATTERN.match(text):
            if active_task is None:
                return "你好，我在。"
            return f"你好，{active_task.title or active_task.task_id} 还在处理中。"
        if active_task is not None:
            return f"我先继续盯着 {active_task.title or active_task.task_id}。如果你要新开任务，直接把目标说清楚就行。"
        return "收到。把目标、约束和期望结果说具体一点，我就可以开始处理。"

    @staticmethod
    def task_created(title: str | None) -> str:
        resolved = str(title or "任务").strip()
        return f"已接收，开始处理 {resolved}。"

    @staticmethod
    def task_resumed(task: RuntimeTaskRecord | None) -> str:
        if task is None:
            return "收到，我继续处理。"
        return f"收到，我继续处理 {task.title or task.task_id}。"

    @staticmethod
    def task_cancelled(task: RuntimeTaskRecord | None) -> str:
        if task is None:
            return "已取消当前任务。"
        return f"已取消 {task.title or task.task_id}。"

    @staticmethod
    def status(task: RuntimeTaskRecord | None) -> str:
        if task is None:
            return "当前没有进行中的任务。"
        status_text = {
            "created": "刚创建",
            "assigned": "已派发",
            "running": "执行中",
            "planned": "已规划",
            "waiting_input": "等你补充信息",
            "reviewing": "审核中",
            "done": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
            "archived": "已归档",
        }.get(task.status.value, task.status.value)
        if task.summary:
            return f"{task.title or task.task_id} 当前{status_text}。{task.summary}"
        if task.last_progress:
            return f"{task.title or task.task_id} 当前{status_text}。{task.last_progress}"
        return f"{task.title or task.task_id} 当前{status_text}。"

    @staticmethod
    def need_input(task: RuntimeTaskRecord | None, payload: TaskNeedInputEventPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        question = payload.input_request.question or "请补充继续执行所需的信息。"
        if payload.summary:
            return f"{prefix} 需要你补充信息。{payload.summary} {question}".strip()
        return f"{prefix} 需要你补充信息。{question}".strip()

    def task_result(self, task: RuntimeTaskRecord | None, payload: TaskResultEventPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        body = payload.result_text or payload.summary or self._join_text_blocks(payload.result_blocks)
        if not body:
            body = "任务已完成。"
        return f"{prefix} 已完成。{body}".strip()

    @staticmethod
    def task_progress(task: RuntimeTaskRecord | None, payload: TaskProgressEventPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        summary = str(payload.summary or "").strip()
        next_step = str(payload.next_step or "").strip()
        if summary and next_step:
            return f"{prefix} 进展：{summary}。下一步：{next_step}".strip()
        if summary:
            return f"{prefix} 进展：{summary}".strip()
        if next_step:
            return f"{prefix} 正在继续处理。下一步：{next_step}".strip()
        return f"{prefix} 正在继续处理。".strip()

    @staticmethod
    def task_failed(task: RuntimeTaskRecord | None, payload: TaskFailedEventPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        reason = payload.reason or payload.summary or "执行失败。"
        return f"{prefix} 失败了。{reason}".strip()

    @staticmethod
    def cancelled_event(task: RuntimeTaskRecord | None, payload: TaskCancelledEventPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        reason = payload.reason or "任务已取消。"
        return f"{prefix} 已取消。{reason}".strip()

    @staticmethod
    def safe_fallback(payload: ReplyBlockedPayload) -> str:
        hint = payload.redaction_hint or "请去掉敏感信息后再试。"
        return f"这条内容我不能直接发出。{hint}"

    @staticmethod
    def _join_text_blocks(blocks: list[ContentBlock]) -> str:
        parts = [str(block.text or "").strip() for block in blocks]
        return "\n".join(part for part in parts if part).strip()


__all__ = ["DialoguePolicy"]
