from __future__ import annotations

import pytest

from emoticorebot.protocol.commands import AssignAgentPayload, BrainReplyPayload, TaskCreatePayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload
from emoticorebot.protocol.priorities import EventPriority, priority_for
from emoticorebot.protocol.task_models import ReplyDraft, TaskStateSnapshot
from emoticorebot.protocol.topics import EventType, Topic


def test_build_envelope_derives_topic_and_default_priority() -> None:
    payload = BrainReplyPayload(
        command_id="cmd_1",
        reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done"),
    )

    event = build_envelope(
        event_type=EventType.BRAIN_REPLY,
        source="brain",
        target="runtime",
        session_id="sess_1",
        payload=payload,
    )

    assert event.topic == Topic.BRAIN_COMMAND
    assert event.priority == EventPriority.P3


def test_business_event_requires_session_id() -> None:
    payload = BrainReplyPayload(
        command_id="cmd_1",
        reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done"),
    )

    with pytest.raises(ValueError):
        BusEnvelope(
            topic=Topic.BRAIN_COMMAND,
            event_type=EventType.BRAIN_REPLY,
            priority=EventPriority.P3,
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
                "status": "assigned",
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
    assert priority_for(EventType.INPUT_INTERRUPT) == EventPriority.P0
    assert priority_for(EventType.TASK_END) == EventPriority.P2
    assert priority_for(EventType.MEMORY_WRITE_REQUEST) == EventPriority.P4
