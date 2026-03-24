"""Data structures for the outer affect dynamics layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


@dataclass(frozen=True, slots=True)
class PADVector:
    """A compact PAD tuple with helper methods."""

    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    @classmethod
    def from_iterable(cls, values: Iterable[float] | None) -> "PADVector":
        data = list(values or [0.0, 0.0, 0.0])[:3]
        while len(data) < 3:
            data.append(0.0)
        return cls(
            pleasure=_clamp(data[0], -1.0, 1.0),
            arousal=_clamp(data[1], -1.0, 1.0),
            dominance=_clamp(data[2], -1.0, 1.0),
        )

    def shifted(self, delta: "PADVector") -> "PADVector":
        return PADVector(
            pleasure=_clamp(self.pleasure + delta.pleasure, -1.0, 1.0),
            arousal=_clamp(self.arousal + delta.arousal, -1.0, 1.0),
            dominance=_clamp(self.dominance + delta.dominance, -1.0, 1.0),
        )

    def to_list(self) -> list[float]:
        return [self.pleasure, self.arousal, self.dominance]

    def to_dict(self) -> dict[str, float]:
        return {
            "pleasure": round(self.pleasure, 6),
            "arousal": round(self.arousal, 6),
            "dominance": round(self.dominance, 6),
        }


@dataclass(frozen=True, slots=True)
class AffectState:
    """Persisted affect state shared by the outer runtime."""

    current_pad: PADVector = field(default_factory=PADVector)
    last_user_pad: PADVector = field(default_factory=PADVector)
    last_delta_pad: PADVector = field(default_factory=PADVector)
    vitality: float = 0.72
    pressure: float = 0.0
    turn_count: int = 0
    updated_at: str = ""

    @classmethod
    def default(cls) -> "AffectState":
        return cls()

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AffectState":
        data = payload or {}
        return cls(
            current_pad=PADVector.from_iterable(_coerce_pad_values(data.get("current_pad"))),
            last_user_pad=PADVector.from_iterable(_coerce_pad_values(data.get("last_user_pad"))),
            last_delta_pad=PADVector.from_iterable(_coerce_pad_values(data.get("last_delta_pad"))),
            vitality=_clamp(_coerce_float(data.get("vitality"), 0.72), 0.0, 1.0),
            pressure=_clamp(_coerce_float(data.get("pressure"), 0.0), -1.0, 1.0),
            turn_count=max(0, int(_coerce_float(data.get("turn_count"), 0))),
            updated_at=str(data.get("updated_at", "") or "").strip(),
        )

    def evolved(
        self,
        *,
        current_pad: PADVector,
        last_user_pad: PADVector,
        last_delta_pad: PADVector,
        vitality: float,
        pressure: float,
    ) -> "AffectState":
        return AffectState(
            current_pad=current_pad,
            last_user_pad=last_user_pad,
            last_delta_pad=last_delta_pad,
            vitality=_clamp(vitality, 0.0, 1.0),
            pressure=_clamp(pressure, -1.0, 1.0),
            turn_count=self.turn_count + 1,
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_pad": self.current_pad.to_dict(),
            "last_user_pad": self.last_user_pad.to_dict(),
            "last_delta_pad": self.last_delta_pad.to_dict(),
            "vitality": round(self.vitality, 6),
            "pressure": round(self.pressure, 6),
            "turn_count": self.turn_count,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class EmotionSignal:
    """A lightweight semantic emotion read for the current user turn."""

    primary_emotion: str = "neutral"
    intensity: float = 0.0
    confidence: float = 0.0
    support_need: str = "quiet_company"
    wants_action: bool = False
    trigger_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_emotion": str(self.primary_emotion or "neutral").strip() or "neutral",
            "intensity": round(_clamp(self.intensity, 0.0, 1.0), 6),
            "confidence": round(_clamp(self.confidence, 0.0, 1.0), 6),
            "support_need": str(self.support_need or "quiet_company").strip() or "quiet_company",
            "wants_action": bool(self.wants_action),
            "trigger_text": str(self.trigger_text or "").strip(),
        }


@dataclass(frozen=True, slots=True)
class AffectTurnResult:
    """One affect evolution result for a user turn."""

    previous_state: AffectState
    state: AffectState
    user_pad: PADVector
    delta_pad: PADVector
    pressure_delta: float
    emotion_signal: EmotionSignal | None = None


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_pad_values(value: Any) -> list[float]:
    if isinstance(value, dict):
        return [
            _coerce_float(value.get("pleasure"), 0.0),
            _coerce_float(value.get("arousal"), 0.0),
            _coerce_float(value.get("dominance"), 0.0),
        ]
    if isinstance(value, (list, tuple)):
        return [_coerce_float(item, 0.0) for item in value[:3]]
    return [0.0, 0.0, 0.0]
