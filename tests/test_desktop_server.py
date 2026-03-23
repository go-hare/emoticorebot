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
        await bridge.start()
        try:
            await bridge._run_turn(socket, {"text": "帮我整理今天的状态"})
            await asyncio.sleep(0)
        finally:
            await bridge.stop()
            await runtime.stop()

        event_types = [message["type"] for message in socket.messages]
        assert "surface_state" in event_types
        assert "reply_chunk" in event_types
        assert "reply_done" in event_types
        assert "affect_state" in event_types

        surface_payloads = [
            message["payload"]
            for message in socket.messages
            if message["type"] == "surface_state"
        ]
        assert surface_payloads[-1]["phase"] == "idle"

        reply_done = next(message for message in socket.messages if message["type"] == "reply_done")
        assert reply_done["payload"]["text"] == "beautified reply"

    asyncio.run(_exercise(tmp_path))
