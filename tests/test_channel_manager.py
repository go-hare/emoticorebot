from __future__ import annotations

import asyncio
import sys
from types import ModuleType

from emoticorebot.channels.base import BaseChannel
from emoticorebot.channels.manager import ChannelManager
from emoticorebot.config.schema import Config
from emoticorebot.runtime.transport_bus import OutboundMessage, TransportBus


class _FakeStreamingChannel(BaseChannel):
    name = "fake"

    def __init__(self, bus: TransportBus) -> None:
        super().__init__(config=object(), bus=bus)
        self.delta_calls: list[tuple[str, dict[str, object]]] = []
        self.final_calls: list[tuple[str, dict[str, object]]] = []
        self.normal_calls: list[str] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        self.normal_calls.append(msg.content)

    async def send_stream_delta(self, msg: OutboundMessage, state: dict[str, object]) -> None:
        state["joined"] = f"{state.get('joined', '')}{msg.content}"
        self.delta_calls.append((msg.content, dict(state)))

    async def send_stream_final(self, msg: OutboundMessage, state: dict[str, object]) -> None:
        self.final_calls.append((msg.content, dict(state)))


class _FakeFallbackChannel(BaseChannel):
    name = "fallback"

    def __init__(self, bus: TransportBus) -> None:
        super().__init__(config=object(), bus=bus)
        self.sent: list[str] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg.content)


async def _exercise_channel_manager_uses_stream_hooks() -> None:
    bus = TransportBus()
    manager = ChannelManager(Config(), bus)
    channel = _FakeStreamingChannel(bus)
    manager.channels = {"fake": channel}

    dispatch = asyncio.create_task(manager._dispatch_outbound())
    try:
        await bus.publish_outbound(
            OutboundMessage(
                channel="fake",
                chat_id="chat_1",
                content="你",
                metadata={"_stream": True, "_stream_id": "stream_1", "_stream_state": "delta"},
            )
        )
        await bus.publish_outbound(
            OutboundMessage(
                channel="fake",
                chat_id="chat_1",
                content="你好。",
                metadata={"_stream": True, "_stream_id": "stream_1", "_stream_state": "final"},
            )
        )

        deadline = asyncio.get_running_loop().time() + 1.0
        while len(channel.final_calls) < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert channel.delta_calls == [("你", {"joined": "你"})]
        assert channel.final_calls == [("你好。", {"joined": "你"})]
        assert manager._stream_states == {}
    finally:
        dispatch.cancel()
        await asyncio.gather(dispatch, return_exceptions=True)


def test_channel_manager_uses_stream_hooks() -> None:
    asyncio.run(_exercise_channel_manager_uses_stream_hooks())


async def _exercise_channel_manager_fallback_stream_suppresses_final_duplicate() -> None:
    bus = TransportBus()
    manager = ChannelManager(Config(), bus)
    channel = _FakeFallbackChannel(bus)
    manager.channels = {"fallback": channel}

    dispatch = asyncio.create_task(manager._dispatch_outbound())
    try:
        await bus.publish_outbound(
            OutboundMessage(
                channel="fallback",
                chat_id="chat_1",
                content="你",
                metadata={"_stream": True, "_stream_id": "stream_1", "_stream_state": "delta"},
            )
        )
        await bus.publish_outbound(
            OutboundMessage(
                channel="fallback",
                chat_id="chat_1",
                content="你好。",
                metadata={"_stream": True, "_stream_id": "stream_1", "_stream_state": "final"},
            )
        )

        deadline = asyncio.get_running_loop().time() + 1.0
        while len(channel.sent) < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert channel.sent == ["你"]
        assert manager._stream_states == {}
    finally:
        dispatch.cancel()
        await asyncio.gather(dispatch, return_exceptions=True)


def test_channel_manager_fallback_stream_suppresses_final_duplicate() -> None:
    asyncio.run(_exercise_channel_manager_fallback_stream_suppresses_final_duplicate())


async def _exercise_channel_manager_clears_interrupted_stream_state_on_followup_message() -> None:
    bus = TransportBus()
    manager = ChannelManager(Config(), bus)
    channel = _FakeStreamingChannel(bus)
    manager.channels = {"fake": channel}

    dispatch = asyncio.create_task(manager._dispatch_outbound())
    try:
        await bus.publish_outbound(
            OutboundMessage(
                channel="fake",
                chat_id="chat_1",
                content="你",
                metadata={"_stream": True, "_stream_id": "stream_1", "_stream_state": "delta"},
            )
        )

        deadline = asyncio.get_running_loop().time() + 1.0
        while len(channel.delta_calls) < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert ("fake", "chat_1", "stream_1") in manager._stream_states

        await bus.publish_outbound(
            OutboundMessage(
                channel="fake",
                chat_id="chat_1",
                content="已完成",
                metadata={},
            )
        )

        deadline = asyncio.get_running_loop().time() + 1.0
        while len(channel.normal_calls) < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert channel.normal_calls == ["已完成"]
        assert manager._stream_states == {}
    finally:
        dispatch.cancel()
        await asyncio.gather(dispatch, return_exceptions=True)


def test_channel_manager_clears_interrupted_stream_state_on_followup_message() -> None:
    asyncio.run(_exercise_channel_manager_clears_interrupted_stream_state_on_followup_message())


class _FakeMatrixChannel(BaseChannel):
    name = "matrix"

    def __init__(self, config, bus, *, restrict_to_workspace: bool = False, workspace=None) -> None:
        super().__init__(config=config, bus=bus)
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace = workspace

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        return None


def test_channel_manager_registers_matrix_channel_when_enabled() -> None:
    bus = TransportBus()
    config = Config()
    config.channels.matrix.enabled = True
    config.tools.restrict_to_workspace = True

    module = ModuleType("emoticorebot.channels.matrix")
    module.MatrixChannel = _FakeMatrixChannel
    original = sys.modules.get("emoticorebot.channels.matrix")
    sys.modules["emoticorebot.channels.matrix"] = module
    try:
        manager = ChannelManager(config, bus)
    finally:
        if original is None:
            sys.modules.pop("emoticorebot.channels.matrix", None)
        else:
            sys.modules["emoticorebot.channels.matrix"] = original

    channel = manager.channels.get("matrix")
    assert isinstance(channel, _FakeMatrixChannel)
    assert channel.restrict_to_workspace is True
    assert channel.workspace == config.workspace_path
