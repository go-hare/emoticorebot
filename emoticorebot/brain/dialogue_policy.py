"""User-visible wording helpers for the executive brain."""

from __future__ import annotations

import re

from emoticorebot.protocol.events import (
    ReplyBlockedPayload,
    TaskAskPayload,
    TaskEndPayload,
    TaskUpdatePayload,
)
from emoticorebot.right.store import RightBrainRecord
from emoticorebot.utils.right_brain_projection import normalize_task_result, normalize_task_state


class DialoguePolicy:
    """Formats concise user-visible replies from runtime state and events."""

    _GREETING_PATTERN = re.compile(
        r"^(你好|您好|嗨|hi|hello|哈喽|在吗|在不在|早上好|中午好|晚上好)[!！。\.~～\s]*$",
        re.IGNORECASE,
    )

    def direct_reply(self, user_input: str, active_task: RightBrainRecord | None) -> str:
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
    def task_resumed(task: RightBrainRecord | None) -> str:
        if task is None:
            return "收到，我继续处理。"
        return f"收到，我继续处理 {task.title or task.task_id}。"

    @staticmethod
    def task_cancelled(task: RightBrainRecord | None) -> str:
        if task is None:
            return "已取消当前任务。"
        return f"已取消 {task.title or task.task_id}。"

    @staticmethod
    def status(task: RightBrainRecord | None) -> str:
        if task is None:
            return "当前没有进行中的任务。"
        state = normalize_task_state(task.state.value)
        result = normalize_task_result(task.state.value, task.result)
        status_text = {
            "running": "执行中",
            "done": {
                "success": "已完成",
                "failed": "失败",
                "cancelled": "已取消",
                "none": "已结束",
            }.get(result, "已结束"),
        }.get(state, "执行中")
        if task.summary:
            return f"{task.title or task.task_id} 当前{status_text}。{task.summary}"
        if task.last_progress:
            return f"{task.title or task.task_id} 当前{status_text}。{task.last_progress}"
        return f"{task.title or task.task_id} 当前{status_text}。"

    @staticmethod
    def task_ask(task: RightBrainRecord | None, payload: TaskAskPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        question = payload.question or "请补充继续执行所需的信息。"
        if payload.why:
            return f"{prefix} 需要你补充信息。{payload.why} {question}".strip()
        return f"{prefix} 需要你补充信息。{question}".strip()

    @staticmethod
    def task_update(task: RightBrainRecord | None, payload: TaskUpdatePayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        message = str(payload.message or "").strip()
        if message:
            return f"{prefix} 进展：{message}".strip()
        return f"{prefix} 正在继续处理。".strip()

    def task_end(self, task: RightBrainRecord | None, payload: TaskEndPayload) -> str:
        prefix = task.title if task is not None else payload.task_id
        if payload.result == "success":
            body = payload.output or payload.summary
            if not body:
                body = "任务已完成。"
            return f"{prefix} 已完成。{body}".strip()
        if payload.result == "cancelled":
            reason = payload.error or payload.summary or "任务已取消。"
            return f"{prefix} 已取消。{reason}".strip()
        reason = payload.error or payload.summary or "执行失败。"
        return f"{prefix} 失败了。{reason}".strip()

    @staticmethod
    def right_brain_accepted(task: RightBrainRecord | None, *, reason: str | None = None) -> str:
        prefix = task.title if task is not None else "这个请求"
        if reason:
            return f"{prefix} 已开始处理。{reason}".strip()
        return f"{prefix} 已开始处理。".strip()

    @staticmethod
    def right_brain_progress(
        task: RightBrainRecord | None,
        *,
        summary: str,
        next_step: str | None = None,
    ) -> str:
        prefix = task.title if task is not None else "这个请求"
        if next_step:
            return f"{prefix} 进展：{summary}。下一步：{next_step}".strip()
        return f"{prefix} 进展：{summary}".strip()

    @staticmethod
    def right_brain_rejected(task: RightBrainRecord | None, *, reason: str) -> str:
        prefix = task.title if task is not None else "这个请求"
        body = reason or "当前无法处理。"
        return f"{prefix} 这次先不继续执行。{body}".strip()

    @staticmethod
    def right_brain_result(
        task: RightBrainRecord | None,
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
        body = result_text or summary or "已经处理好了。"
        return f"{prefix} 已完成。{body}".strip()

    @staticmethod
    def safe_fallback(payload: ReplyBlockedPayload) -> str:
        hint = payload.redaction_hint or "请去掉敏感信息后再试。"
        return f"这条内容我不能直接发出。{hint}"


__all__ = ["DialoguePolicy"]
