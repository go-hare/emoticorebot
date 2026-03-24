"""Companion-layer orchestration between kernel truth and outer expression."""

from emoticorebot.companion.models import CompanionIntent, SurfaceExpression
from emoticorebot.companion.orchestrator import build_companion_surface

__all__ = [
    "CompanionIntent",
    "SurfaceExpression",
    "build_companion_surface",
]
