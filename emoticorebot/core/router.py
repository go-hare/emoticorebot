"""Fusion Router - 图节点路由决策

根据当前 FusionState 决定下一个执行节点。

路由规则：
  - done=True → "memory"（EQ 已生成回复）
  - iq.task 存在 且 无 result 且 无 error → "iq"（执行/重试任务）
  - iq.result 或 iq.error 存在 → "eq"（审核结果）
  - 兜底 → "memory"
"""

from __future__ import annotations

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """统一处理 dict 和对象属性访问"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class FusionRouter:
    """LangGraph 节点路由器"""

    def __init__(self, max_iterations: int = 10, max_iq_attempts: int = 3):
        self.max_iterations = max_iterations
        self.max_iq_attempts = max_iq_attempts

    def route_next(self, state: dict) -> str:
        """
        根据当前状态决定下一个节点。

        :param state: FusionState
        :return: 节点名 "eq" | "iq" | "memory"
        """
        done: bool = state.get("done", False)
        iq = state.get("iq", {})

        iq_task: str = _get(iq, "task", "")
        iq_result: str = _get(iq, "result", "")
        iq_error: str = _get(iq, "error", "")
        iq_attempts: int = _get(iq, "attempts", 0)

        # 1. EQ 已完成 → 进入收尾
        if done:
            return "memory"

        # 2. IQ 尝试次数超限 → 强制结束
        if iq_attempts >= self.max_iq_attempts:
            return "memory"

        # 3. 有任务待执行（首次或重试）→ IQ 执行
        #    条件：task 存在 且 没有 result 且 没有 error
        if iq_task and not iq_result and not iq_error:
            return "iq"

        # 4. IQ 有结果或错误 → EQ 审核
        if iq_result or iq_error:
            return "eq"

        # 5. 兜底：结束
        return "memory"


__all__ = ["FusionRouter"]
