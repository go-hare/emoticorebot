from __future__ import annotations

from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload, TaskEndPayload
from emoticorebot.protocol.task_models import ContentBlock, MessageRef, ReplyDraft
from emoticorebot.protocol.topics import EventType
from emoticorebot.safety.guard import SafetyGuard


def test_safety_guard_redacts_sensitive_reply() -> None:
    guard = SafetyGuard()
    event = build_envelope(
        event_type=EventType.OUTPUT_REPLY_READY,
        source="brain",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        correlation_id="turn_1",
        payload=ReplyReadyPayload(
            reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="api_key=sk-abcdefghijklmnopqrstuv"),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        ),
    )

    result = guard.guard_reply_event(event)

    assert result.decision == "redact"
    assert result.blocked is None
    assert result.event is not None
    assert result.event.event_type == EventType.OUTPUT_REPLY_REDACTED
    assert result.event.payload.reply.plain_text == "api_key=[REDACTED]"


def test_safety_guard_redacts_sensitive_reply_blocks() -> None:
    guard = SafetyGuard()
    event = build_envelope(
        event_type=EventType.OUTPUT_REPLY_READY,
        source="brain",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        correlation_id="turn_1",
        payload=ReplyReadyPayload(
            reply=ReplyDraft(
                reply_id="reply_2",
                kind="answer",
                content_blocks=[ContentBlock(type="text", text="password=secret123")],
            ),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        ),
    )

    result = guard.guard_reply_event(event)

    assert result.decision == "redact"
    assert result.event is not None
    assert result.event.payload.reply.content_blocks[0].text == "password=[REDACTED]"


def test_safety_guard_blocks_private_key_reply() -> None:
    guard = SafetyGuard()
    event = build_envelope(
        event_type=EventType.OUTPUT_REPLY_READY,
        source="brain",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        correlation_id="turn_1",
        payload=ReplyReadyPayload(
            reply=ReplyDraft(
                reply_id="reply_3",
                kind="answer",
                plain_text="-----BEGIN PRIVATE KEY-----",
            ),
            origin_message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        ),
    )

    result = guard.guard_reply_event(event)

    assert result.decision == "block"
    assert result.event is None
    assert result.blocked is not None
    assert result.blocked.policy_name == "secret_filter"


def test_safety_guard_redacts_sensitive_task_output() -> None:
    guard = SafetyGuard()
    event = build_envelope(
        event_type=EventType.TASK_END,
        source="runtime",
        target="broadcast",
        session_id="sess_1",
        turn_id="turn_1",
        task_id="task_1",
        correlation_id="task_1",
        payload=TaskEndPayload(
            task_id="task_1",
            result="success",
            summary="done",
            output="api_key=sk-abcdefghijklmnopqrstuv",
            error="password=secret123",
        ),
    )

    guarded = guard.guard_task_event(event)

    assert guarded.payload.output == "api_key=[REDACTED]"
    assert guarded.payload.error == "password=[REDACTED]"
