from __future__ import annotations

from emoticorebot.affect import AffectState, PADVector
from emoticorebot.companion.expression import build_surface_expression
from emoticorebot.companion.intent import build_companion_intent
from emoticorebot.companion.models import CompanionIntent


def test_companion_intent_prefers_comfort_when_user_is_low_and_still_needs_help() -> None:
    intent = build_companion_intent(
        user_text="我好累，帮我看看日志",
        kernel_output="kernel raw for: 我好累，帮我看看日志",
    )

    assert intent.mode == "comfort"
    assert intent.warmth >= 0.90
    assert intent.initiative >= 0.40


def test_companion_intent_uses_quiet_company_for_short_presence_ping() -> None:
    intent = build_companion_intent(
        user_text="在吗",
        kernel_output="kernel raw for: 在吗",
    )

    assert intent.mode == "quiet_company"
    assert intent.warmth >= 0.88
    assert intent.intensity < 0.30


def test_companion_expression_softens_low_intensity_comfort_motion() -> None:
    expression = build_surface_expression(
        CompanionIntent(
            mode="comfort",
            warmth=0.96,
            initiative=0.42,
            intensity=0.28,
        )
    )

    assert expression.motion_hint == "stay_close"
    assert expression.body_state == "resting_close"
    assert expression.settling_phase == "resting"


def test_companion_expression_can_make_quiet_company_more_present() -> None:
    expression = build_surface_expression(
        CompanionIntent(
            mode="quiet_company",
            warmth=0.90,
            initiative=0.52,
            intensity=0.26,
        )
    )

    assert expression.motion_hint == "small_nod"
    assert expression.body_state == "listening_beside"
    assert expression.settling_phase == "listening"


def test_companion_biases_toward_comfort_when_affect_pressure_is_high() -> None:
    intent = build_companion_intent(
        user_text="在吗",
        kernel_output="kernel raw for: 在吗",
        affect_state=AffectState(
            current_pad=PADVector(pleasure=-0.30, arousal=0.20, dominance=-0.12),
            vitality=0.33,
            pressure=0.58,
        ),
    )

    assert intent.mode == "comfort"
    assert intent.warmth >= 0.95


def test_companion_expression_rests_more_when_affect_vitality_is_low() -> None:
    expression = build_surface_expression(
        CompanionIntent(
            mode="focused",
            warmth=0.88,
            initiative=0.52,
            intensity=0.34,
        ),
        affect_state=AffectState(
            current_pad=PADVector(pleasure=-0.12, arousal=0.18, dominance=0.06),
            vitality=0.24,
            pressure=0.44,
        ),
    )

    assert expression.motion_hint == "minimal"
    assert expression.body_state == "resting_beside"
    assert expression.idle_phase == "resting"
