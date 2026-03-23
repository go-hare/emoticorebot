from __future__ import annotations

import asyncio

from emoticorebot.desktop import DesktopStateAdapter, build_desktop_state_packet
from emoticorebot.runtime.scheduler import RuntimeScheduler
from tests.test_runtime_scheduler import FakeFront, FakeKernel


def test_build_desktop_state_packet_for_replying_focused_state() -> None:
    packet = build_desktop_state_packet(
        {
            "thread_id": "thread-1",
            "phase": "replying",
            "mode": "focused",
            "presence": "beside",
            "expression": "attentive_warm",
            "motion_hint": "small_nod",
            "body_state": "steady_listening",
            "breathing_hint": "steady_even",
            "linger_hint": "remain_available",
            "recommended_hold_ms": 0,
        }
    )

    payload = packet.as_payload()
    assert payload["avatar_phase"] == "speaking"
    assert payload["animation"] == "speak_clear"
    assert payload["bubble_mode"] == "speaking"
    assert payload["bubble_visible"] is True
    assert payload["mood"] == "steady"


def test_build_desktop_state_packet_for_settling_quiet_company_state() -> None:
    packet = build_desktop_state_packet(
        {
            "thread_id": "thread-2",
            "phase": "settling",
            "mode": "quiet_company",
            "presence": "beside",
            "expression": "soft_smile",
            "motion_hint": "stay_close",
            "body_state": "resting_beside",
            "breathing_hint": "soft_slow",
            "linger_hint": "quiet_stay",
            "recommended_hold_ms": 900,
        }
    )

    payload = packet.as_payload()
    assert payload["avatar_phase"] == "settling"
    assert payload["animation"] == "settle_soft"
    assert payload["bubble_mode"] == "fading"
    assert payload["hold_ms"] == 900
    assert payload["mood"] == "calm"


def test_desktop_state_adapter_tracks_latest_packet_per_thread() -> None:
    adapter = DesktopStateAdapter()

    adapter.ingest_surface_state({"thread_id": "thread-3", "phase": "listening"})
    adapter.ingest_surface_state(
        {
            "thread_id": "thread-3",
            "phase": "idle",
            "breathing_hint": "soft_slow",
            "body_state": "resting_beside",
        }
    )

    latest = adapter.get_thread_packet("thread-3")
    assert latest is not None
    assert latest["phase"] == "idle"
    assert latest["animation"] == "idle_breathe_soft"


def test_desktop_state_adapter_queues_packets_for_async_shells() -> None:
    async def _exercise() -> None:
        adapter = DesktopStateAdapter()
        await adapter.handle_surface_state(
            {
                "thread_id": "thread-4",
                "phase": "replying",
                "mode": "encourage",
                "expression": "happy_gentle",
                "motion_hint": "nod",
            }
        )

        packet = await adapter.next_packet()
        assert packet["thread_id"] == "thread-4"
        assert packet["animation"] == "speak_bright"
        assert packet["bubble_visible"] is True

    asyncio.run(_exercise())


def test_desktop_state_adapter_can_consume_runtime_surface_updates() -> None:
    async def _exercise() -> None:
        adapter = DesktopStateAdapter()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=FakeFront(), kernel=FakeKernel())

        reply = await runtime.handle_user_text(
            thread_id="thread-desktop",
            session_id="thread-desktop",
            user_id="user-1",
            user_text="帮我看看日志",
            stream_handler=None,
            surface_state_handler=adapter.handle_surface_state,
        )

        assert reply == "beautified reply"
        latest = adapter.get_thread_packet("thread-desktop")
        assert latest is not None
        assert latest["phase"] == "idle"
        assert latest["avatar_phase"] == "idle"
        assert latest["animation"] == "idle_breathe_even"

        await runtime.stop()

    from pathlib import Path

    asyncio.run(_exercise())
