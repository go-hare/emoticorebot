"""Agent package exports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "DeepAgentExecutor",
    "ExecutiveBrain",
    "ToolManager",
]


def __getattr__(name: str) -> Any:
    if name == "DeepAgentExecutor":
        from emoticorebot.execution.deep_agent_executor import DeepAgentExecutor

        return DeepAgentExecutor
    if name == "ExecutiveBrain":
        from emoticorebot.brain.executive import ExecutiveBrain

        return ExecutiveBrain
    if name == "ToolManager":
        from emoticorebot.agent.tool import ToolManager

        return ToolManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
