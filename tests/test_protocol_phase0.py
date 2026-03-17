from __future__ import annotations

import pytest

from emoticorebot.protocol.commands import AssignAgentPayload, TaskCreatePayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload, StableInputPayload
from emoticorebot.protocol.priorities import EventPriority, priority_for
from emoticorebot.protocol.task_models import MessageRef, ReplyDraft, TaskStateSnapshot
from emoticorebot.protocol.topics import EventType, Topic


def test_build_envelope_derives_topic_and_default_priority() -> None:
    payload = StableInputPayload(
        input_id="turn_1",
        input_kind="text",
        channel_kind="chat",
        message=MessageRef(channel="cli", chat_id="direct", message_id="msg_1"),
        plain_text="done",
    )

    event = build_envelope(
        event_type=EventType.INPUT_STABLE,
        source="input_normalizer",
        target="broadcast",
        session_id="sess_1",
        payload=payload,
    )

    assert event.topic == Topic.INPUT_EVENT
    assert event.priority == EventPriority.P1


def test_business_event_requires_session_id() -> None:
    payload = TaskCreatePayload(command_id="cmd_1", request="write a report")

    with pytest.raises(ValueError):
        BusEnvelope(
            topic=Topic.TASK_COMMAND,
            event_type=EventType.TASK_CREATE,
            priority=EventPriority.P1,
            source="brain",
            target="runtime",
            payload=payload,
        )


def test_nested_payloads_validate_against_document_models() -> None:
    event = TaskCreatePayload.model_validate(
        {
            "command_id": "cmd_1",
            "request": "write a report",
            "goal": "produce a concise report",
            "context": {
                "title": "report",
                "review_policy": "required",
                "origin_message": {
                    "channel": "cli",
                    "chat_id": "direct",
                    "message_id": "msg_1",
                },
            },
        }
    )

    assert event.request == "write a report"
    assert event.context["origin_message"]["message_id"] == "msg_1"


def test_assign_agent_payload_uses_typed_nested_models() -> None:
    payload = AssignAgentPayload.model_validate(
        {
            "assignment_id": "assign_1",
            "task_id": "task_1",
            "agent_role": "worker",
            "task_state": {
                "task_id": "task_1",
                "state": "running",
                "state_version": 2,
            },
            "task_request": {
                "request": "fix the failing tests",
                "constraints": ["keep changes small"],
            },
        }
    )

    assert isinstance(payload.task_state, TaskStateSnapshot)
    assert payload.task_request is not None
    assert payload.task_request.constraints == ["keep changes small"]


def test_safe_fallback_is_nested_inside_reply_draft() -> None:
    payload = ReplyReadyPayload.model_validate(
        {
            "reply": {
                "reply_id": "reply_2",
                "kind": "safety_fallback",
                "plain_text": "I cannot share that.",
                "safe_fallback": True,
            }
        }
    )

    assert payload.reply.safe_fallback is True


def test_priority_mapping_matches_document_examples() -> None:
    assert priority_for(EventType.TASK_CANCEL) == EventPriority.P0
    assert priority_for(EventType.TASK_END) == EventPriority.P2
    assert priority_for(EventType.MEMORY_WRITE_REQUEST) == EventPriority.P4
