from __future__ import annotations

from emoticorebot.io.normalizer import InputNormalizer
from emoticorebot.protocol.priorities import EventPriority
from emoticorebot.protocol.task_models import ContentBlock
from emoticorebot.protocol.topics import EventType, Topic


def test_input_normalizer_emits_input_stable_event() -> None:
    normalizer = InputNormalizer()

    event = normalizer.normalize_text_message(
        session_id="sess_1",
        turn_id="turn_1",
        channel="cli",
        chat_id="direct",
        sender_id="user",
        message_id="msg_1",
        content="你好",
        metadata={"source": "test"},
    )

    assert event.event_type == EventType.INPUT_STABLE
    assert event.topic == Topic.INPUT_EVENT
    assert event.priority == EventPriority.P1
    assert event.payload.channel_kind == "chat"
    assert event.payload.plain_text == "你好"


def test_input_normalizer_preserves_barge_in_flag() -> None:
    normalizer = InputNormalizer()

    event = normalizer.normalize_text_message(
        session_id="sess_2",
        turn_id="turn_2",
        channel="cli",
        chat_id="direct",
        sender_id="user",
        message_id="msg_2",
        content="打断一下",
        barge_in=True,
    )

    assert event.payload.barge_in is True


def test_input_normalizer_emits_voice_stable_event() -> None:
    normalizer = InputNormalizer()

    event = normalizer.normalize_voice_message(
        session_id="sess_voice",
        turn_id="turn_voice",
        channel="rtc",
        chat_id="call_1",
        sender_id="user",
        message_id="voice_1",
        transcript="我想补充一句",
        attachments=[ContentBlock(type="audio", path="/tmp/input.wav", mime_type="audio/wav")],
        metadata={"source": "asr"},
    )

    assert event.event_type == EventType.INPUT_STABLE
    assert event.topic == Topic.INPUT_EVENT
    assert event.priority == EventPriority.P1
    assert event.payload.input_kind == "voice"
    assert event.payload.channel_kind == "voice"
    assert event.payload.plain_text == "我想补充一句"
    assert event.payload.attachments[0].type == "audio"


def test_input_normalizer_emits_video_multimodal_turn() -> None:
    normalizer = InputNormalizer()

    event = normalizer.normalize_video_turn(
        session_id="sess_video",
        turn_id="turn_video",
        channel="rtc",
        chat_id="video_1",
        sender_id="user",
        message_id="video_msg_1",
        plain_text="看一下这个界面",
        content_blocks=[
            ContentBlock(type="image", path="/tmp/frame.jpg", mime_type="image/jpeg"),
            ContentBlock(type="text", text="画面里有一个错误弹窗"),
        ],
        metadata={"source": "multimodal"},
    )

    assert event.event_type == EventType.INPUT_STABLE
    assert event.payload.input_kind == "multimodal"
    assert event.payload.channel_kind == "video"
    assert event.payload.plain_text == "看一下这个界面"
    assert len(event.payload.content_blocks) == 2
