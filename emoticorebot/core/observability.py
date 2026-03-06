"""Observability - 可观测性管理（span / trace）

提供轻量的可观测性接口，用于记录节点执行和路由决策。
当前为 no-op 实现，可在需要时接入 OpenTelemetry 等后端。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator


class ObservabilitySpan:
    """单个 span（no-op 实现）"""

    def __init__(self, name: str, attrs: dict[str, Any] | None = None):
        self.name = name
        self.attrs = attrs or {}

    def __enter__(self) -> "ObservabilitySpan":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class ObservabilityContext:
    """单次请求的可观测性上下文"""

    def span(self, name: str, attrs: dict[str, Any] | None = None) -> ObservabilitySpan:
        """创建一个新的 span（no-op）"""
        return ObservabilitySpan(name, attrs)

    def record_route(self, from_node: str, to_node: str) -> None:
        """记录节点路由（no-op）"""
        pass


class ObservabilityManager:
    """可观测性管理器"""

    def get_current_context(self) -> ObservabilityContext | None:
        """获取当前请求的可观测性上下文，无上下文时返回 None"""
        return None


_manager = ObservabilityManager()


def get_observability_manager() -> ObservabilityManager:
    """获取全局可观测性管理器（单例）"""
    return _manager


__all__ = [
    "ObservabilitySpan",
    "ObservabilityContext",
    "ObservabilityManager",
    "get_observability_manager",
]
