"""Compose the first-pass companion layer for one turn."""

from __future__ import annotations

from emoticorebot.affect import AffectState, EmotionSignal
from emoticorebot.companion.expression import build_surface_expression
from emoticorebot.companion.intent import build_companion_intent
from emoticorebot.companion.models import CompanionIntent, SurfaceExpression


def build_companion_surface(
    *,
    user_text: str,
    kernel_output: str,
    affect_state: AffectState | None = None,
    emotion_signal: EmotionSignal | None = None,
) -> tuple[CompanionIntent, SurfaceExpression]:
    """Build both intent and surface expression for a turn."""

    intent = build_companion_intent(
        user_text=user_text,
        kernel_output=kernel_output,
        affect_state=affect_state,
        emotion_signal=emotion_signal,
    )
    expression = build_surface_expression(intent, affect_state=affect_state)
    return intent, expression
