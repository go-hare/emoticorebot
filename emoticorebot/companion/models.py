"""Small companion-layer data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CompanionIntent:
    """How this turn should accompany the user."""

    mode: str
    warmth: float
    initiative: float
    intensity: float


@dataclass(slots=True)
class SurfaceExpression:
    """How the companion intent should look and feel externally."""

    text_style: str
    presence: str
    expression: str
    motion_hint: str
    body_state: str
    breathing_hint: str
    linger_hint: str
    speaking_phase: str
    settling_phase: str
    idle_phase: str
