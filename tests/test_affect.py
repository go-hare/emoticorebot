from __future__ import annotations

from pathlib import Path

from emoticorebot.affect import AffectRuntime, AffectState, AffectStateStore, PADVector


class FakePredictor:
    def predict_delta_pad(
        self,
        *,
        user_pad: PADVector,
        vitality: float,
        current_pad: PADVector,
    ) -> PADVector:
        _ = vitality, current_pad
        return PADVector(
            pleasure=max(-0.2, min(0.2, user_pad.pleasure * 0.5)),
            arousal=max(-0.2, min(0.2, user_pad.arousal * 0.5 + 0.04)),
            dominance=max(-0.2, min(0.2, user_pad.dominance * 0.5)),
        )


def test_affect_store_round_trips_in_memony_directory(tmp_path: Path) -> None:
    store = AffectStateStore(tmp_path)
    state = AffectState(
        current_pad=PADVector(pleasure=0.12, arousal=-0.08, dominance=0.22),
        last_user_pad=PADVector(pleasure=-0.20, arousal=0.10, dominance=-0.10),
        last_delta_pad=PADVector(pleasure=0.04, arousal=-0.02, dominance=0.03),
        vitality=0.63,
        pressure=0.18,
        turn_count=7,
        updated_at="2026-03-23T23:59:00",
    )

    store.save(state)

    assert store.path == tmp_path / "memony" / "affect_state.json"
    assert store.load() == state


def test_affect_runtime_evolves_and_persists_state(tmp_path: Path) -> None:
    runtime = AffectRuntime(
        store=AffectStateStore(tmp_path),
        predictor=FakePredictor(),
    )

    result = runtime.evolve(user_text="我好累，帮我看看日志")
    loaded = runtime.load_state()

    assert result.state.turn_count == 1
    assert result.state.current_pad.pleasure < 0.0
    assert result.state.pressure > 0.0
    assert 0.0 <= result.state.vitality <= 1.0
    assert loaded.turn_count == result.state.turn_count
    assert loaded.pressure == round(result.state.pressure, 6)
    assert loaded.vitality == round(result.state.vitality, 6)
    assert loaded.current_pad.to_dict() == result.state.current_pad.to_dict()
