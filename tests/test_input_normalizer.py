from __future__ import annotations

from emoticorebot.io.normalizer import InputNormalizer
from emoticorebot.protocol.priorities import EventPriority
from emoticorebot.protocol.task_models import ContentBlock
from emoticorebot.protocol.topics import EventType, Topic


def test_input_normalizer_emits_turn_received_event() -> None:
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

    assert event.event_type == EventType.INPUT_TURN_RECEIVED
    assert event.topic == Topic.INPUT_EVENT
    assert event.priority == EventPriority.P1
    assert event.payload.input_mode == "turn"
    assert event.payload.session_mode == "turn_chat"
    assert event.payload.channel_kind == "chat"
    assert event.payload.input_kind == "text"
    assert event.payload.user_text == "你好"
    assert event.payload.input_slots.user == "你好"
    assert event.payload.input_slots.task == ""


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


def test_input_normalizer_emits_voice_turn_received_event() -> None:
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

    assert event.event_type == EventType.INPUT_TURN_RECEIVED
    assert event.topic == Topic.INPUT_EVENT
    assert event.priority == EventPriority.P1
    assert event.payload.session_mode == "realtime_chat"
    assert event.payload.channel_kind == "voice"
    assert event.payload.input_kind == "voice"
    assert event.payload.user_text == "我想补充一句"
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

    assert event.event_type == EventType.INPUT_TURN_RECEIVED
    assert event.payload.session_mode == "realtime_chat"
    assert event.payload.channel_kind == "video"
    assert event.payload.input_kind == "multimodal"
    assert event.payload.user_text == "看一下这个界面"
    assert len(event.payload.content_blocks) == 2


def test_input_normalizer_emits_stream_events() -> None:
    normalizer = InputNormalizer()

    started = normalizer.normalize_stream_start(
        session_id="sess_stream",
        stream_id="stream_1",
        channel="rtc",
        chat_id="call_1",
        sender_id="user",
        message_id="msg_stream_1",
        channel_kind="voice",
        input_kind="voice",
        metadata={"barge_in": True},
    )
    chunk = normalizer.normalize_stream_chunk(
        session_id="sess_stream",
        stream_id="stream_1",
        chunk_index=0,
        chunk_text="你好",
    )
    committed = normalizer.normalize_stream_commit(
        session_id="sess_stream",
        turn_id="turn_stream_1",
        stream_id="stream_1",
        committed_text="你好呀",
        metadata={"history_context": "recent"},
    )
    interrupted = normalizer.normalize_stream_interrupted(
        session_id="sess_stream",
        stream_id="stream_1",
        reason="barge_in",
    )

    assert started.event_type == EventType.INPUT_STREAM_STARTED
    assert started.topic == Topic.INPUT_EVENT
    assert started.priority == EventPriority.P1
    assert started.payload.input_mode == "stream"
    assert started.payload.session_mode == "realtime_chat"
    assert started.payload.metadata["channel_kind"] == "voice"
    assert started.payload.metadata["input_kind"] == "voice"
    assert started.payload.metadata["barge_in"] is True

    assert chunk.event_type == EventType.INPUT_STREAM_CHUNK
    assert chunk.topic == Topic.INPUT_EVENT
    assert chunk.priority == EventPriority.P1
    assert chunk.payload.chunk_index == 0
    assert chunk.payload.chunk_text == "你好"

    assert committed.event_type == EventType.INPUT_STREAM_COMMITTED
    assert committed.topic == Topic.INPUT_EVENT
    assert committed.priority == EventPriority.P1
    assert committed.turn_id == "turn_stream_1"
    assert committed.payload.committed_text == "你好呀"
    assert committed.payload.metadata["history_context"] == "recent"

    assert interrupted.event_type == EventType.INPUT_STREAM_INTERRUPTED
    assert interrupted.topic == Topic.INPUT_EVENT
    assert interrupted.priority == EventPriority.P0
    assert interrupted.payload.reason == "barge_in"
