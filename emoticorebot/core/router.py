"""Fusion Router - 图节点路由决策

根据当前 FusionState 决定下一个执行节点。

路由规则：
  来自 eq_node：
    - done=True  → "memory"（EQ 已生成回复，进入收尾）
    - done=False, iq.task 有值, iq.attempts=0 → "iq"（委托给 IQ）
  来自 iq_node：
    - iq.attempts 超限 → "memory"（强制结束，防止死循环）
    - 否则 → "eq"（IQ 结果交回 EQ 处理）
  兜底 → "memory"
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
        iq_attempts: int = _get(iq, "attempts", 0)

        # IQ 尝试次数超限 → 强制结束
        if iq_attempts >= self.max_iq_attempts:
            return "memory"

        # EQ 已完成（生成了回复）→ 进入收尾
        if done:
            return "memory"

        # EQ 首次委托给 IQ（task 已设置但 IQ 尚未执行）
        if iq_task and iq_attempts == 0:
            return "iq"

        # IQ 已执行（有结果或需要追问）→ 交回 EQ 处理
        if iq_attempts > 0:
            return "eq"

        # 兜底：结束
        return "memory"


__all__ = ["FusionRouter"]
