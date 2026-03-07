"""Fusion Router - 图节点路由决策。"""

from __future__ import annotations

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """统一处理 dict 和对象属性访问"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class FusionRouter:
    """LangGraph 节点路由器"""

    def __init__(self, max_iq_attempts: int = 3):
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
        iq_status: str = _get(iq, "status", "")
        iq_attempts: int = _get(iq, "attempts", 0)

        # 1. EQ 已完成 → 进入收尾
        if done:
            return "memory"

        # 2. IQ 尝试次数超限 → 交给 EQ 做最终收束
        if iq_attempts >= self.max_iq_attempts:
            return "eq"

        # 3. EQ 已发出新的内部问题 → IQ 执行
        if iq_task and iq_status in {"queued", "running"}:
            return "iq"

        # 4. IQ 已完成一次分析包 → 回到 EQ 综合判断
        if iq_status in {"completed", "needs_input", "uncertain", "failed"}:
            return "eq"

        # 5. 兜底：结束
        return "memory"


__all__ = ["FusionRouter"]
