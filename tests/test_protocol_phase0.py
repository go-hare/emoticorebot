from __future__ import annotations

import pytest

from emoticorebot.protocol.commands import ExecutionTaskRequestPayload, MainBrainReplyRequestPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.event_contracts import PAYLOAD_MODEL_BY_EVENT_TYPE
from emoticorebot.protocol.events import DeliveryTargetPayload, OutputInlineReadyPayload, TurnInputPayload
from emoticorebot.protocol.priorities import EventPriority, priority_for
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft
from emoticorebot.protocol.topics import EventType, Topic


def test_build_envelope_derives_topic_and_default_priority() -> None:
    payload = TurnInputPayload(
        input_id="turn_1",
        input_mode="turn",
        session_mode="turn_chat",
        message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        user_text="done",
    )

    event = build_envelope(
        event_type=EventType.INPUT_TURN_RECEIVED,
        source="input_normalizer",
        target="broadcast",
        session_id="sess_1",
        payload=payload,
    )

    assert event.topic == Topic.INPUT_EVENT
    assert event.priority == EventPriority.P1


def test_business_event_requires_session_id() -> None:
    payload = ExecutionTaskRequestPayload(
        job_id="job_1",
        job_action="create_task",
        request_text="write a report",
        delivery_target=DeliveryTargetPayload(delivery_mode="push", channel="cli", chat_id="direct"),
    )

    with pytest.raises(ValueError):
        BusEnvelope(
            topic=Topic.EXECUTION_COMMAND,
            event_type=EventType.EXECUTION_COMMAND_TASK_REQUESTED,
            priority=EventPriority.P1,
            source="main_brain",
            target="execution_runtime",
            payload=payload,
        )


def test_nested_payloads_validate_against_document_models() -> None:
    payload = ExecutionTaskRequestPayload.model_validate(
        {
            "job_id": "job_1",
            "job_action": "create_task",
            "request_text": "write a report",
            "goal": "produce a concise report",
            "delivery_target": {
                "delivery_mode": "push",
                "channel": "cli",
                "chat_id": "direct",
            },
            "context": {
                "title": "report",
                "origin_message": {
                    "channel": "cli",
                    "chat_id": "direct",
                    "message_id": "msg_1",
                },
            },
        }
    )

    assert payload.request_text == "write a report"
    assert payload.delivery_target == DeliveryTargetPayload(delivery_mode="push", channel="cli", chat_id="direct")
    assert payload.context["origin_message"]["message_id"] == "msg_1"


def test_main_brain_reply_request_uses_typed_nested_models() -> None:
    payload = MainBrainReplyRequestPayload.model_validate(
        {
            "request_id": "main_brain_req_1",
            "turn_input": {
                "input_id": "turn_1",
                "input_mode": "turn",
                "session_mode": "turn_chat",
                "message": {
                    "channel": "cli",
                    "chat_id": "direct",
                    "message_id": "msg_1",
                },
                "user_text": "fix the failing tests",
                "metadata": {"channel_kind": "chat"},
            },
        }
    )

    assert isinstance(payload.turn_input, TurnInputPayload)
    assert payload.turn_input.message.message_id == "msg_1"
    assert payload.followup_context is None


def test_safe_fallback_is_nested_inside_reply_draft() -> None:
    payload = OutputInlineReadyPayload.model_validate(
        {
            "output_id": "out_2",
            "delivery_target": {
                "delivery_mode": "inline",
                "channel": "cli",
                "chat_id": "direct",
            },
            "content": {
                "reply_id": "reply_2",
                "kind": "safety_fallback",
                "plain_text": "I cannot share that.",
                "safe_fallback": True,
            },
        }
    )

    assert payload.content.safe_fallback is True


def test_priority_mapping_matches_document_examples() -> None:
    assert priority_for(EventType.CONTROL_STOP) == EventPriority.P0
    assert priority_for(EventType.EXECUTION_EVENT_RESULT_READY) == EventPriority.P2
    assert priority_for(EventType.REFLECTION_WRITE_REQUEST) == EventPriority.P4


def test_every_known_event_type_has_payload_contract() -> None:
    known_event_types = {str(event_type) for event_type in EventType}
    assert known_event_types <= set(PAYLOAD_MODEL_BY_EVENT_TYPE)


def test_build_envelope_rejects_event_payload_type_mismatch() -> None:
    wrong_payload = TurnInputPayload(
        input_id="turn_2",
        input_mode="turn",
        session_mode="turn_chat",
        message=MessageRef(channel="cli", chat_id="direct", message_id="msg_2"),
        user_text="hello",
    )

    with pytest.raises(ValueError, match="does not match expected"):
        build_envelope(
            event_type=EventType.EXECUTION_COMMAND_TASK_REQUESTED,
            source="main_brain",
            target="execution_runtime",
            session_id="sess_2",
            payload=wrong_payload,
        )


