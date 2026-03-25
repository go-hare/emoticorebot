"""Desktop adapter for turning surface-state events into UI-friendly packets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class DesktopStatePacket:
    """A compact packet that a desktop shell can consume directly."""

    thread_id: str
    phase: str
    avatar_phase: str
    animation: str
    bubble_mode: str
    bubble_visible: bool
    hold_ms: int
    companion_mode: str
    mood: str
    presence: str
    body_state: str
    breathing_hint: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        """Return a plain dict payload for UI transports."""

        return {
            "thread_id": self.thread_id,
            "phase": self.phase,
            "avatar_phase": self.avatar_phase,
            "animation": self.animation,
            "bubble_mode": self.bubble_mode,
            "bubble_visible": self.bubble_visible,
            "hold_ms": self.hold_ms,
            "companion_mode": self.companion_mode,
            "mood": self.mood,
            "presence": self.presence,
            "body_state": self.body_state,
            "breathing_hint": self.breathing_hint,
            "metadata": dict(self.metadata),
        }


class DesktopStateAdapter:
    """Keep the latest desktop packet per thread and stream updates to a shell."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._latest_by_thread: dict[str, dict[str, Any]] = {}

    def ingest_surface_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Convert one runtime surface-state frame into a desktop payload."""

        payload = build_desktop_state_packet(state).as_payload()
        self._latest_by_thread[payload["thread_id"]] = dict(payload)
        return payload

    async def handle_surface_state(self, state: Mapping[str, Any]) -> None:
        """Runtime callback that stores and queues the next desktop payload."""

        payload = self.ingest_surface_state(state)
        await self._queue.put(payload)

    async def next_packet(self) -> dict[str, Any]:
        """Wait for the next desktop payload."""

        return await self._queue.get()

    def get_thread_packet(self, thread_id: str) -> dict[str, Any] | None:
        """Return the latest packet for one thread, if any."""

        packet = self._latest_by_thread.get(thread_id)
        if packet is None:
            return None
        return dict(packet)


def build_desktop_state_packet(state: Mapping[str, Any]) -> DesktopStatePacket:
    """Map runtime surface-state fields into a desktop-shell packet."""

    thread_id = str(state.get("thread_id", "") or "").strip() or "unknown"
    phase = _normalize_phase(state.get("phase"))
    companion_mode = str(state.get("mode", "quiet_company") or "quiet_company").strip() or "quiet_company"
    mood = _derive_mood(
        companion_mode=companion_mode,
        expression=str(state.get("expression", "") or "").strip(),
    )

    return DesktopStatePacket(
        thread_id=thread_id,
        phase=phase,
        avatar_phase=_avatar_phase_for(phase),
        animation=_animation_for(
            phase=phase,
            mood=mood,
            motion_hint=str(state.get("motion_hint", "") or "").strip(),
            breathing_hint=str(state.get("breathing_hint", "") or "").strip(),
            linger_hint=str(state.get("linger_hint", "") or "").strip(),
        ),
        bubble_mode=_bubble_mode_for(phase),
        bubble_visible=phase in {"replying", "settling"},
        hold_ms=_coerce_int(state.get("recommended_hold_ms"), default=0),
        companion_mode=companion_mode,
        mood=mood,
        presence=str(state.get("presence", "beside") or "beside").strip() or "beside",
        body_state=str(state.get("body_state", "resting_beside") or "resting_beside").strip()
        or "resting_beside",
        breathing_hint=str(state.get("breathing_hint", "soft_slow") or "soft_slow").strip() or "soft_slow",
        metadata={
            "text_style": str(state.get("text_style", "") or "").strip(),
            "expression": str(state.get("expression", "") or "").strip(),
            "motion_hint": str(state.get("motion_hint", "") or "").strip(),
            "linger_hint": str(state.get("linger_hint", "") or "").strip(),
            "lifecycle_phase": str(state.get("lifecycle_phase", "") or "").strip(),
            "affect_vitality": state.get("affect_vitality"),
            "affect_pressure": state.get("affect_pressure"),
            "affect_updated_at": str(state.get("affect_updated_at", "") or "").strip(),
        },
    )


def _normalize_phase(value: Any) -> str:
    phase = str(value or "").strip().lower()
    if phase in {"listening", "replying", "settling", "idle"}:
        return phase
    return "idle"


def _derive_mood(*, companion_mode: str, expression: str) -> str:
    if companion_mode == "comfort" or expression == "gentle_caring":
        return "soothing"
    if companion_mode == "encourage" or expression == "happy_gentle":
        return "bright"
    if companion_mode == "playful" or expression == "playful_soft":
        return "playful"
    if companion_mode == "focused" or expression == "attentive_warm":
        return "steady"
    return "calm"


def _avatar_phase_for(phase: str) -> str:
    return {
        "listening": "listening",
        "replying": "speaking",
        "settling": "settling",
        "idle": "idle",
    }[phase]


def _bubble_mode_for(phase: str) -> str:
    return {
        "listening": "hidden",
        "replying": "speaking",
        "settling": "fading",
        "idle": "hidden",
    }[phase]


def _animation_for(
    *,
    phase: str,
    mood: str,
    motion_hint: str,
    breathing_hint: str,
    linger_hint: str,
) -> str:
    if phase == "listening":
        return "listen_nod" if "nod" in motion_hint else "listen_still"
    if phase == "replying":
        return {
            "soothing": "speak_soft",
            "bright": "speak_bright",
            "playful": "speak_playful",
            "steady": "speak_clear",
            "calm": "speak_gentle",
        }[mood]
    if phase == "settling":
        if linger_hint in {"stay_near", "quiet_stay"}:
            return "settle_soft"
        if linger_hint == "remain_available":
            return "settle_ready"
        return "settle_light"
    if breathing_hint in {"soft_slow", "slow_deep"}:
        return "idle_breathe_soft"
    if breathing_hint == "steady_even":
        return "idle_breathe_even"
    return "idle_breathe_light"


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default
