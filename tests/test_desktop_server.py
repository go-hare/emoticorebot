from __future__ import annotations

import asyncio
import json
from pathlib import Path

from emoticorebot.desktop.server import DesktopBridgeServer, load_affect_state_snapshot
from emoticorebot.runtime.scheduler import RuntimeScheduler
from tests.test_runtime_scheduler import FakeFront, FakeKernel


class FakeSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))


async def _wait_for_messages(
    socket: FakeSocket,
    *,
    event_type: str,
    expected_count: int,
    timeout: float = 1.0,
) -> list[dict[str, object]]:
    async def _poll() -> list[dict[str, object]]:
        while True:
            matches = [message for message in socket.messages if message["type"] == event_type]
            if len(matches) >= expected_count:
                return matches
            await asyncio.sleep(0)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def test_load_affect_state_snapshot_reads_memony_file(tmp_path: Path) -> None:
    memony_dir = tmp_path / "memony"
    memony_dir.mkdir(parents=True)
    (memony_dir / "affect_state.json").write_text(
        json.dumps({"vitality": 0.61, "pressure": 0.27}),
        encoding="utf-8",
    )

    snapshot = load_affect_state_snapshot(tmp_path)

    assert snapshot == {"vitality": 0.61, "pressure": 0.27}


def test_desktop_bridge_server_streams_packets_and_replies(tmp_path: Path) -> None:
    async def _exercise(workspace: Path) -> None:
        (workspace / "memony").mkdir(parents=True, exist_ok=True)
        (workspace / "memony" / "affect_state.json").write_text(
            json.dumps({"vitality": 0.45, "pressure": 0.18}),
            encoding="utf-8",
        )

        runtime = RuntimeScheduler(workspace=workspace, front=FakeFront(), kernel=FakeKernel())
        bridge = DesktopBridgeServer(runtime=runtime, workspace=workspace)
        socket = FakeSocket()
        bridge._connections.add(socket)
        await runtime.start()
        await bridge.start()
        try:
            await bridge._run_turn(socket, {"text": "帮我整理今天的状态"})
            reply_done_messages = await _wait_for_messages(
                socket,
                event_type="reply_done",
                expected_count=2,
            )
            surface_messages = await _wait_for_messages(
                socket,
                event_type="surface_state",
                expected_count=3,
            )
        finally:
            await bridge.stop()
            await runtime.stop()

        event_types = [message["type"] for message in socket.messages]
        assert "surface_state" in event_types
        assert "reply_chunk" in event_types
        assert "reply_done" in event_types
        assert "affect_state" in event_types
        assert "front_hint" not in event_types

        reply_chunk_payloads = [
            message["payload"]
            for message in socket.messages
            if message["type"] == "reply_chunk"
        ]
        assert [payload["chunk"] for payload in reply_chunk_payloads[:2]] == [
            "front live hint",
            "beautified reply",
        ]

        surface_payloads = [message["payload"] for message in surface_messages]
        assert surface_payloads[0]["phase"] == "listening"
        assert surface_payloads[-1]["phase"] == "idle"

        assert [message["payload"]["text"] for message in reply_done_messages] == [
            "front live hint",
            "beautified reply",
        ]

    asyncio.run(_exercise(tmp_path))
