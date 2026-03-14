"""Agent package exports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "CompanionBrain",
    "EventNarrator",
    "CentralExecutor",
    "MemoryService",
    "ReflectionCoordinator",
    "ToolManager",
]


def __getattr__(name: str) -> Any:
    if name == "CompanionBrain":
        from emoticorebot.brain.companion_brain import CompanionBrain

        return CompanionBrain
    if name == "EventNarrator":
        from emoticorebot.brain.event_narrator import EventNarrator

        return EventNarrator
    if name == "CentralExecutor":
        from emoticorebot.execution.central_executor import CentralExecutor

        return CentralExecutor
    if name == "MemoryService":
        from emoticorebot.agent.reflection import MemoryService

        return MemoryService
    if name == "ReflectionCoordinator":
        from emoticorebot.agent.reflection import ReflectionCoordinator

        return ReflectionCoordinator
    if name == "ToolManager":
        from emoticorebot.agent.tool import ToolManager

        return ToolManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
