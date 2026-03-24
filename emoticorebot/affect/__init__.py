"""Affect dynamics layer backed by the local Chordia ONNX model."""

from emoticorebot.affect.models import AffectState, AffectTurnResult, EmotionSignal, PADVector
from emoticorebot.affect.pad_estimator import estimate_user_pad
from emoticorebot.affect.runtime import (
    AffectRuntime,
    ChordiaOnnxRunner,
    create_affect_runtime,
)
from emoticorebot.affect.semantic import infer_emotion_signal
from emoticorebot.affect.store import AffectStateStore

__all__ = [
    "AffectRuntime",
    "AffectState",
    "AffectStateStore",
    "AffectTurnResult",
    "ChordiaOnnxRunner",
    "EmotionSignal",
    "PADVector",
    "create_affect_runtime",
    "estimate_user_pad",
    "infer_emotion_signal",
]
