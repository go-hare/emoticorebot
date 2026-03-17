"""Task-oriented turn classification for the executive brain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from emoticorebot.runtime.state_machine import TaskState
from emoticorebot.runtime.task_store import RuntimeTaskRecord

TurnAction = Literal["create_task", "resume_task", "cancel_task", "status", "reply"]


@dataclass(slots=True)
class TurnDirective:
    action: TurnAction
    task_id: str | None = None
    title: str | None = None


class TaskPolicy:
    """Lightweight decision rules for task-oriented turns."""

    _TASK_PATTERN = re.compile(
        r"(写|创建|新建|生成|修改|更新|编辑|新增|添加|删除|重构|修复|运行|执行|测试|安装|查找|搜索|"
        r"create|write|modify|update|edit|add|delete|refactor|fix|run|test|install|search)",
        re.IGNORECASE,
    )
    _CODE_PATTERN = re.compile(
        r"(```|`[^`]+`|"
        r"\bdef\b|\bclass\b|\bfunction\b|\breturn\b|"
        r"\bpytest\b|\bpip\b|\bnpm\b|\buv\b|\bpython\b|"
        r"[A-Za-z0-9_\-./\\\\]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md|txt|toml|ini|cfg|sh|ps1))",
        re.IGNORECASE,
    )
    _STATUS_PATTERN = re.compile(
        r"(创建好了吗|创建完了吗|好了吗|好了没|完成了吗|完成没|做完了吗|进度|结果呢|怎么样了|还在吗|"
        r"status|progress|done|finished)",
        re.IGNORECASE,
    )
    _CANCEL_PATTERN = re.compile(r"(取消|不用了|先不用|算了|停下|停止|stop|cancel)", re.IGNORECASE)
    _QUESTION_PATTERN = re.compile(
        r"(\?|？|什么|为啥|为什么|怎么|咋|如何|哪里|哪儿|哪个|谁|吗|呢|么|是否|能不能|可不可以|"
        r"what|why|how|when|where|who)",
        re.IGNORECASE,
    )
    _FILE_REF_PATTERN = re.compile(
        r"([A-Za-z0-9_\-./\\\\]+\.(py|js|ts|tsx|jsx|json|yaml|yml|md|txt|toml|ini|cfg|sh|ps1))",
        re.IGNORECASE,
    )

    def decide(self, user_input: str, tasks: list[RuntimeTaskRecord]) -> TurnDirective:
        text = str(user_input or "").strip()
        waiting = self._latest_task(tasks, state=TaskState.WAITING)
        active = self._latest_active_task(tasks)

        if self._STATUS_PATTERN.search(text) and (waiting is not None or active is not None):
            target = waiting or active
            return TurnDirective(action="status", task_id=target.task_id if target is not None else None)

        if self._CANCEL_PATTERN.search(text) and (waiting is not None or active is not None):
            target = waiting or active
            return TurnDirective(action="cancel_task", task_id=target.task_id if target is not None else None)

        if waiting is not None:
            if self._QUESTION_PATTERN.search(text):
                return TurnDirective(action="status", task_id=waiting.task_id)
            return TurnDirective(action="resume_task", task_id=waiting.task_id)

        if self._looks_like_task_request(text):
            return TurnDirective(action="create_task", title=self.derive_title(text))

        if active is not None and self._looks_like_task_reference(text, active):
            return TurnDirective(action="status", task_id=active.task_id)

        return TurnDirective(action="reply")

    @classmethod
    def derive_title(cls, request: str) -> str:
        text = str(request or "").strip()
        if not text:
            return "执行任务"
        file_match = cls._FILE_REF_PATTERN.search(text)
        file_name = file_match.group(1) if file_match is not None else ""
        action = ""
        if re.search(r"(修改|更新|编辑|update|modify|edit)", text, re.IGNORECASE):
            action = "修改"
        elif re.search(r"(写|创建|新建|生成|新增|添加|create|write|add)", text, re.IGNORECASE):
            action = "创建"
        elif re.search(r"(删除|delete)", text, re.IGNORECASE):
            action = "删除"
        elif re.search(r"(运行|执行|run|execute)", text, re.IGNORECASE):
            action = "执行"
        elif re.search(r"(测试|test)", text, re.IGNORECASE):
            action = "测试"
        if action and file_name:
            return f"{action} {file_name}"
        return text[:32]

    @classmethod
    def _looks_like_task_request(cls, text: str) -> bool:
        return bool(cls._TASK_PATTERN.search(text) and (cls._CODE_PATTERN.search(text) or len(text) >= 12))

    @staticmethod
    def _latest_task(
        tasks: list[RuntimeTaskRecord],
        *,
        state: TaskState,
    ) -> RuntimeTaskRecord | None:
        for task in reversed(tasks):
            if task.state is state:
                return task
        return None

    @staticmethod
    def _latest_active_task(tasks: list[RuntimeTaskRecord]) -> RuntimeTaskRecord | None:
        for task in reversed(tasks):
            if task.state is not TaskState.DONE:
                return task
        return None

    @staticmethod
    def _looks_like_task_reference(text: str, task: RuntimeTaskRecord) -> bool:
        lowered = text.lower()
        return task.task_id.lower() in lowered or task.title.lower() in lowered


__all__ = ["TaskPolicy", "TurnAction", "TurnDirective"]
