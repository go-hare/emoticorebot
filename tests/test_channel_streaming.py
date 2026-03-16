from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from emoticorebot.channels.feishu import FeishuChannel
from emoticorebot.channels.telegram import TelegramChannel
from emoticorebot.config.schema import FeishuConfig, TelegramConfig
from emoticorebot.runtime.transport_bus import OutboundMessage, TransportBus


async def _exercise_telegram_stream_updates_in_place() -> None:
    channel = TelegramChannel(TelegramConfig(reply_to_message=True), TransportBus())
    channel._app = SimpleNamespace(bot=object())

    send_calls: list[tuple[int, str, object]] = []
    edit_calls: list[tuple[int, int, str]] = []

    async def fake_send_text_message(*, chat_id: int, text: str, reply_params=None):
        send_calls.append((chat_id, text, reply_params))
        return SimpleNamespace(message_id=321)

    async def fake_edit_text_message(*, chat_id: int, message_id: int, text: str) -> None:
        edit_calls.append((chat_id, message_id, text))

    channel._send_text_message = fake_send_text_message  # type: ignore[method-assign]
    channel._edit_text_message = fake_edit_text_message  # type: ignore[method-assign]
    channel._stop_typing = lambda _chat_id: None  # type: ignore[method-assign]

    state: dict[str, object] = {}
    base = {
        "channel": "telegram",
        "chat_id": "123",
        "metadata": {"message_id": 999},
    }

    await channel.send_stream_delta(OutboundMessage(content="你", **base), state)
    await channel.send_stream_delta(OutboundMessage(content="好", **base), state)
    await channel.send_stream_final(OutboundMessage(content="你好。", **base), state)

    assert send_calls == [(123, "你", send_calls[0][2])]
    assert send_calls[0][2] is not None
    assert edit_calls == [(123, 321, "你好"), (123, 321, "你好。")]
    assert state["message_id"] == 321
    assert state["rendered_text"] == "你好"


def test_telegram_stream_updates_in_place() -> None:
    asyncio.run(_exercise_telegram_stream_updates_in_place())


async def _exercise_feishu_stream_updates_in_place() -> None:
    channel = FeishuChannel(FeishuConfig(), TransportBus())
    channel._client = object()

    send_calls: list[tuple[str, str, str, str]] = []
    update_calls: list[tuple[str, str, str]] = []

    def fake_send_message_sync(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> str | None:
        send_calls.append((receive_id_type, receive_id, msg_type, content))
        return "om_message_1"

    def fake_update_message_sync(message_id: str, msg_type: str, content: str) -> bool:
        update_calls.append((message_id, msg_type, content))
        return True

    channel._send_message_sync = fake_send_message_sync  # type: ignore[method-assign]
    channel._update_message_sync = fake_update_message_sync  # type: ignore[method-assign]

    state: dict[str, object] = {}
    base = {
        "channel": "feishu",
        "chat_id": "ou_user_1",
        "metadata": {},
    }

    await channel.send_stream_delta(OutboundMessage(content="你", **base), state)
    await channel.send_stream_delta(OutboundMessage(content="好", **base), state)
    await channel.send_stream_final(OutboundMessage(content="你好。", **base), state)

    assert len(send_calls) == 1
    assert send_calls[0][:3] == ("open_id", "ou_user_1", "interactive")
    created_card = json.loads(send_calls[0][3])
    assert created_card["elements"][0]["content"] == "你"

    assert len(update_calls) == 2
    assert update_calls[0][:2] == ("om_message_1", "interactive")
    assert update_calls[1][:2] == ("om_message_1", "interactive")
    assert json.loads(update_calls[0][2])["elements"][0]["content"] == "你好"
    assert json.loads(update_calls[1][2])["elements"][0]["content"] == "你好。"
    assert state["message_id"] == "om_message_1"
    assert state["rendered_text"] == "你好"


def test_feishu_stream_updates_in_place() -> None:
    asyncio.run(_exercise_feishu_stream_updates_in_place())


def test_matrix_edit_payload_shape() -> None:
    pytest.importorskip("nio")
    from emoticorebot.channels.matrix import _build_matrix_edit_content

    content = _build_matrix_edit_content("你好。", target_event_id="$event_1")

    assert content["body"] == "* 你好。"
    assert content["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$event_1"}
    assert content["m.new_content"]["body"] == "你好。"


async def _exercise_matrix_stream_updates_in_place() -> None:
    pytest.importorskip("nio")
    from emoticorebot.channels.matrix import MatrixChannel
    from emoticorebot.config.schema import MatrixConfig

    channel = MatrixChannel(MatrixConfig(), TransportBus())
    channel.client = object()

    sent_payloads: list[tuple[str, dict[str, object]]] = []

    async def fake_stop_typing_keepalive(room_id: str, *, clear_typing: bool) -> None:
        return None

    async def fake_send_room_content(room_id: str, content: dict[str, object]) -> str | None:
        sent_payloads.append((room_id, content))
        if len(sent_payloads) == 1:
            return "$event_1"
        return None

    channel._stop_typing_keepalive = fake_stop_typing_keepalive  # type: ignore[method-assign]
    channel._send_room_content = fake_send_room_content  # type: ignore[method-assign]

    state: dict[str, object] = {}
    base = {
        "channel": "matrix",
        "chat_id": "!room:example.org",
        "metadata": {},
    }

    await channel.send_stream_delta(OutboundMessage(content="你", **base), state)
    await channel.send_stream_delta(OutboundMessage(content="好", **base), state)
    await channel.send_stream_final(OutboundMessage(content="你好。", **base), state)

    assert len(sent_payloads) == 3
    assert sent_payloads[0][0] == "!room:example.org"
    assert sent_payloads[0][1]["body"] == "你"
    assert sent_payloads[1][1]["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$event_1"}
    assert sent_payloads[1][1]["m.new_content"]["body"] == "你好"
    assert sent_payloads[2][1]["m.new_content"]["body"] == "你好。"
    assert state["event_id"] == "$event_1"
    assert state["rendered_text"] == "你好"


def test_matrix_stream_updates_in_place() -> None:
    asyncio.run(_exercise_matrix_stream_updates_in_place())
