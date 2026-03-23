"""Map companion intent to outer expression hints."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from emoticorebot.companion.models import CompanionIntent, SurfaceExpression

if TYPE_CHECKING:
    from emoticorebot.affect import AffectState

_SURFACE_PRESETS: dict[str, SurfaceExpression] = {
    "comfort": SurfaceExpression(
        text_style="soft_wrap",
        presence="close",
        expression="gentle_caring",
        motion_hint="small_tilt",
        body_state="soothing_close",
        breathing_hint="slow_deep",
        linger_hint="stay_near",
        speaking_phase="replying",
        settling_phase="settling",
        idle_phase="resting",
    ),
    "encourage": SurfaceExpression(
        text_style="bright_warm",
        presence="near",
        expression="happy_gentle",
        motion_hint="nod",
        body_state="upright_bright",
        breathing_hint="light_lift",
        linger_hint="hold_warmth",
        speaking_phase="replying",
        settling_phase="settling",
        idle_phase="idle_ready",
    ),
    "playful": SurfaceExpression(
        text_style="lively_warm",
        presence="close",
        expression="playful_soft",
        motion_hint="bounce",
        body_state="bouncy_close",
        breathing_hint="quick_light",
        linger_hint="spark_then_stay",
        speaking_phase="replying",
        settling_phase="settling",
        idle_phase="idle_ready",
    ),
    "focused": SurfaceExpression(
        text_style="warm_clear",
        presence="beside",
        expression="attentive_warm",
        motion_hint="small_nod",
        body_state="steady_listening",
        breathing_hint="steady_even",
        linger_hint="remain_available",
        speaking_phase="replying",
        settling_phase="listening",
        idle_phase="idle_ready",
    ),
    "quiet_company": SurfaceExpression(
        text_style="soft_calm",
        presence="beside",
        expression="soft_smile",
        motion_hint="stay_close",
        body_state="resting_beside",
        breathing_hint="soft_slow",
        linger_hint="quiet_stay",
        speaking_phase="replying",
        settling_phase="settling",
        idle_phase="resting",
    ),
}


def build_surface_expression(
    intent: CompanionIntent,
    affect_state: "AffectState | None" = None,
) -> SurfaceExpression:
    """Translate a companionship intent into minimal external style hints."""

    expression = _SURFACE_PRESETS.get(intent.mode, _SURFACE_PRESETS["quiet_company"])

    if intent.mode == "comfort" and intent.intensity <= 0.34:
        expression = replace(
            expression,
            motion_hint="stay_close",
            body_state="resting_close",
            settling_phase="resting",
        )
    if intent.mode == "focused" and intent.warmth >= 0.90:
        expression = replace(
            expression,
            presence="near",
            body_state="leaning_in",
            settling_phase="listening",
        )
    if intent.mode == "quiet_company" and intent.initiative >= 0.48:
        expression = replace(
            expression,
            motion_hint="small_nod",
            body_state="listening_beside",
            settling_phase="listening",
        )
    if affect_state is not None:
        expression = _apply_affect_bias(expression, intent=intent, affect_state=affect_state)
    return expression


def _apply_affect_bias(
    expression: SurfaceExpression,
    *,
    intent: CompanionIntent,
    affect_state: "AffectState",
) -> SurfaceExpression:
    if affect_state.vitality <= 0.32:
        return replace(
            expression,
            presence="close" if intent.mode == "comfort" else "beside",
            motion_hint="stay_close" if intent.mode in {"comfort", "quiet_company"} else "minimal",
            body_state="resting_close" if intent.mode == "comfort" else "resting_beside",
            breathing_hint="soft_slow",
            settling_phase="resting",
            idle_phase="resting",
        )
    if affect_state.pressure >= 0.42:
        return replace(
            expression,
            motion_hint="stay_close",
            breathing_hint="slow_deep" if intent.mode == "comfort" else "steady_even",
            presence="close" if intent.mode == "comfort" else expression.presence,
        )
    if affect_state.current_pad.arousal >= 0.40 and intent.mode in {"encourage", "playful"}:
        return replace(
            expression,
            breathing_hint="light_lift",
            motion_hint="bounce" if intent.mode == "playful" else "nod",
        )
    if affect_state.current_pad.dominance >= 0.35 and intent.mode == "focused":
        return replace(
            expression,
            body_state="leaning_in",
            motion_hint="small_nod",
        )
    return expression
