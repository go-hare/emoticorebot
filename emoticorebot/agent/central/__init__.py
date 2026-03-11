"""Central agent exports."""

from __future__ import annotations

from typing import Any

__all__ = ["CentralAgentService"]


def __getattr__(name: str) -> Any:
    if name == "CentralAgentService":
        from emoticorebot.agent.central.central import CentralAgentService

        return CentralAgentService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
