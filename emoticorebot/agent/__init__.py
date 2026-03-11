"""Agent package exports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "BrainService",
    "CentralAgentService",
    "MemoryService",
    "ToolManager",
]


def __getattr__(name: str) -> Any:
    if name == "BrainService":
        from emoticorebot.agent.brain import BrainService

        return BrainService
    if name == "CentralAgentService":
        from emoticorebot.agent.central.central import CentralAgentService

        return CentralAgentService
    if name == "MemoryService":
        from emoticorebot.agent.reflection import MemoryService

        return MemoryService
    if name == "ToolManager":
        from emoticorebot.agent.tool import ToolManager

        return ToolManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
