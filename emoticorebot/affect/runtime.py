"""Chordia-backed affect evolution runtime."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import numpy as np

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover - optional dependency at import time
    ort = None

from emoticorebot.affect.models import AffectState, AffectTurnResult, PADVector
from emoticorebot.affect.pad_estimator import estimate_user_pad
from emoticorebot.affect.semantic import infer_emotion_signal
from emoticorebot.affect.store import AffectStateStore

_EXTERNAL_DATA_RE = re.compile(r"\[\"(?P<path>.+?\.onnx\.data)\"\]")
_DEFAULT_CHORDIA_FILE = "chordia_v0.0.1-alpha.onnx"


class DeltaPadPredictor(Protocol):
    """Predict a PAD delta from user PAD, vitality, and current PAD."""

    def predict_delta_pad(
        self,
        *,
        user_pad: PADVector,
        vitality: float,
        current_pad: PADVector,
    ) -> PADVector:
        ...


class ChordiaOnnxRunner:
    """Thin ONNX wrapper around the local Chordia model."""

    def __init__(self, model_path: Path) -> None:
        if ort is None:
            raise RuntimeError("onnxruntime is not installed, cannot load Chordia ONNX.")

        self.model_path = Path(model_path).expanduser().resolve()
        self.session = self._build_session()
        self.input_name = self.session.get_inputs()[0].name

    def predict_delta_pad(
        self,
        *,
        user_pad: PADVector,
        vitality: float,
        current_pad: PADVector,
    ) -> PADVector:
        features = np.array(
            [
                user_pad.pleasure,
                user_pad.arousal,
                user_pad.dominance,
                _clamp(vitality, 0.0, 1.0),
                current_pad.pleasure,
                current_pad.arousal,
                current_pad.dominance,
            ],
            dtype=np.float32,
        ).reshape(1, 7)
        output = self.session.run(None, {self.input_name: features})[0][0]
        return PADVector.from_iterable(
            [
                _clamp(float(output[0]), -0.5, 0.5),
                _clamp(float(output[1]), -0.5, 0.5),
                _clamp(float(output[2]), -0.5, 0.5),
            ]
        )

    def _build_session(self) -> "ort.InferenceSession":
        try:
            return ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        except Exception as exc:
            if self._repair_external_data_reference(str(exc)):
                return ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
            raise RuntimeError(f"Failed to load Chordia ONNX at {self.model_path}: {exc}") from exc

    def _repair_external_data_reference(self, error_text: str) -> bool:
        match = _EXTERNAL_DATA_RE.search(error_text)
        if match is None:
            return False

        missing_path = Path(match.group("path"))
        if missing_path.exists():
            return False

        candidates = sorted(self.model_path.parent.glob("*.onnx.data"))
        if len(candidates) != 1:
            return False

        missing_path.symlink_to(candidates[0])
        return True


class AffectRuntime:
    """Keep a simple global affect state in sync with incoming user turns."""

    def __init__(
        self,
        *,
        store: AffectStateStore,
        predictor: DeltaPadPredictor,
    ) -> None:
        self.store = store
        self.predictor = predictor
        self.store.ensure()

    def load_state(self) -> AffectState:
        return self.store.load()

    def evolve(self, *, user_text: str) -> AffectTurnResult:
        previous_state = self.store.load()
        user_pad = estimate_user_pad(user_text)
        delta_pad = self.predictor.predict_delta_pad(
            user_pad=user_pad,
            vitality=previous_state.vitality,
            current_pad=previous_state.current_pad,
        )
        current_pad = previous_state.current_pad.shifted(delta_pad)
        pressure_delta = _compute_pressure_delta(delta_pad)
        pressure = _blend(
            previous_state.pressure,
            _clamp(previous_state.pressure + pressure_delta, -1.0, 1.0),
            weight=0.46,
        )
        vitality_target = _compute_vitality_target(
            current_pad=current_pad,
            pressure=pressure,
            previous_vitality=previous_state.vitality,
        )
        vitality = _blend(previous_state.vitality, vitality_target, weight=0.35)
        next_state = previous_state.evolved(
            current_pad=current_pad,
            last_user_pad=user_pad,
            last_delta_pad=delta_pad,
            vitality=vitality,
            pressure=pressure,
        )
        emotion_signal = infer_emotion_signal(user_text=user_text, affect_state=next_state)
        self.store.save(next_state)
        return AffectTurnResult(
            previous_state=previous_state,
            state=next_state,
            user_pad=user_pad,
            delta_pad=delta_pad,
            pressure_delta=pressure_delta,
            emotion_signal=emotion_signal,
        )
def create_affect_runtime(workspace: Path, model_path: Path | str | None) -> AffectRuntime | None:
    """Create a Chordia-backed affect runtime from an explicit model path."""

    if model_path is None or ort is None:
        return None

    resolved = Path(model_path).expanduser()
    if resolved.is_dir():
        resolved = resolved / _DEFAULT_CHORDIA_FILE
    if not resolved.exists():
        return None

    store = AffectStateStore(workspace)
    predictor = ChordiaOnnxRunner(resolved)
    return AffectRuntime(store=store, predictor=predictor)


def _compute_pressure_delta(delta_pad: PADVector) -> float:
    value = (-1.0 * delta_pad.pleasure) + (0.8 * delta_pad.arousal) + (-0.6 * delta_pad.dominance)
    return _clamp(value, -1.0, 1.0)


def _compute_vitality_target(
    *,
    current_pad: PADVector,
    pressure: float,
    previous_vitality: float,
) -> float:
    target = (
        0.64
        + 0.16 * current_pad.pleasure
        - 0.18 * max(0.0, pressure)
        + 0.08 * max(0.0, current_pad.dominance)
        - 0.06 * max(0.0, current_pad.arousal)
    )
    if pressure <= -0.20:
        target += 0.04
    if previous_vitality <= 0.28:
        target += 0.03
    return _clamp(target, 0.18, 0.98)


def _blend(previous: float, current: float, *, weight: float) -> float:
    return _clamp(previous + ((current - previous) * weight), -1.0, 1.0)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))
