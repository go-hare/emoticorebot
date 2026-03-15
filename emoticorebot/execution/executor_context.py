"""Execution-layer dependency container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from emoticorebot.tools import ToolRegistry

if TYPE_CHECKING:
    from emoticorebot.agent.context import ContextBuilder


@dataclass(frozen=True)
class ExecutorContext:
    """Immutable dependencies used by an executor instance."""

    worker_llm: Any
    tool_registry: ToolRegistry | None
    context_builder: "ContextBuilder"


__all__ = ["ExecutorContext"]
