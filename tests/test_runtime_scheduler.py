from __future__ import annotations

from emoticorebot.protocol.commands import (
    BrainCancelTaskPayload,
    BrainCreateTaskPayload,
    BrainReplyPayload,
    BrainResumeTaskPayload,
)
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import (
    DeliveryFailedPayload,
    RepliedPayload,
    TaskApprovedReportPayload,
    TaskNeedInputReportPayload,
    TaskResultReportPayload,
    TaskStartedReportPayload,
)
from emoticorebot.protocol.task_models import InputRequest, MessageRef, ReplyDraft
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.scheduler import RuntimeScheduler


def _origin_message() -> MessageRef:
    return MessageRef(channel="cli", chat_id="direct", message_id="msg_1")


def test_create_task_emits_created_then_assigned_then_assign_command() -> None:
    scheduler = RuntimeScheduler()
    command = build_envelope(
        event_type=EventType.BRAIN_CREATE_TASK,
        source="brain",
        target="runtime",
        session_id="sess_1",
        payload=BrainCreateTaskPayload(
            command_id="cmd_1",
            request="fix the bug",
            preferred_agent="worker",
            origin_message=_origin_message(),
        ),
    )

    outputs = scheduler.dispatch(command)

    assert [event.event_type for event in outputs] == [
        EventType.TASK_EVENT_CREATED,
        EventType.TASK_EVENT_ASSIGNED,
        EventType.RUNTIME_ASSIGN_AGENT,
    ]
    assert outputs[1].payload.agent_role == "worker"


def test_need_input_then_resume_emits_assigned_and_resume_command() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CREATE_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainCreateTaskPayload(command_id="cmd_1", request="collect more info", origin_message=_origin_message()),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[2].payload.assignment_id

    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_STARTED,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskStartedReportPayload(
                task_id=task_id,
                agent_role="worker",
                assignment_id=assignment_id,
            ),
        )
    )
    need_input = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_NEED_INPUT,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskNeedInputReportPayload(
                task_id=task_id,
                agent_role="worker",
                assignment_id=assignment_id,
                input_request=InputRequest(field="city", question="Which city?"),
            ),
        )
    )
    assert need_input[0].event_type == EventType.TASK_EVENT_NEED_INPUT

    resumed = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_RESUME_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=BrainResumeTaskPayload(
                command_id="cmd_resume",
                task_id=task_id,
                user_input="Shanghai",
                origin_message=_origin_message(),
            ),
        )
    )

    assert [event.event_type for event in resumed] == [
        EventType.TASK_EVENT_ASSIGNED,
        EventType.RUNTIME_RESUME_AGENT,
    ]


def test_review_flow_emits_reviewing_then_approved_then_result() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CREATE_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainCreateTaskPayload(
                command_id="cmd_1",
                request="write deployment plan",
                review_policy="required",
                origin_message=_origin_message(),
            ),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[2].payload.assignment_id

    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_STARTED,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskStartedReportPayload(task_id=task_id, agent_role="worker", assignment_id=assignment_id),
        )
    )
    review_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_RESULT,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskResultReportPayload(
                task_id=task_id,
                agent_role="worker",
                assignment_id=assignment_id,
                summary="draft complete",
                result_text="deployment plan",
                reviewer_required=True,
            ),
        )
    )

    assert [event.event_type for event in review_outputs] == [
        EventType.TASK_EVENT_REVIEWING,
        EventType.RUNTIME_ASSIGN_AGENT,
    ]

    approved_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_APPROVED,
            source="reviewer",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskApprovedReportPayload(
                task_id=task_id,
                review_id=review_outputs[1].payload.reviewer_context.review_id,
                summary="looks good",
            ),
        )
    )

    assert [event.event_type for event in approved_outputs] == [
        EventType.TASK_EVENT_APPROVED,
        EventType.TASK_EVENT_RESULT,
    ]
    assert approved_outputs[1].payload.result_text == "deployment plan"


def test_cancel_from_assigned_emits_cancelled_and_cancel_agent() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CREATE_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainCreateTaskPayload(command_id="cmd_1", request="stop me later", origin_message=_origin_message()),
        )
    )
    task_id = create_outputs[0].task_id

    cancelled = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CANCEL_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=BrainCancelTaskPayload(command_id="cmd_cancel", task_id=task_id, reason="user_requested"),
        )
    )

    assert [event.event_type for event in cancelled] == [
        EventType.TASK_EVENT_CANCELLED,
        EventType.RUNTIME_CANCEL_AGENT,
    ]


def test_reply_command_becomes_reply_ready() -> None:
    scheduler = RuntimeScheduler()
    outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_REPLY,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainReplyPayload(
                command_id="cmd_reply",
                reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done"),
                origin_message=_origin_message(),
            ),
        )
    )

    assert len(outputs) == 1
    assert outputs[0].event_type == EventType.OUTPUT_REPLY_READY
    assert outputs[0].payload.reply.reply_id == "reply_1"


def test_reply_command_for_terminal_task_does_not_archive_before_delivery_ack() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CREATE_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainCreateTaskPayload(command_id="cmd_1", request="ship it", origin_message=_origin_message()),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[2].payload.assignment_id

    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_STARTED,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskStartedReportPayload(task_id=task_id, agent_role="worker", assignment_id=assignment_id),
        )
    )
    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_RESULT,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskResultReportPayload(
                task_id=task_id,
                agent_role="worker",
                assignment_id=assignment_id,
                summary="done",
                result_text="done",
            ),
        )
    )

    outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_REPLY,
            source="brain",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=BrainReplyPayload(
                command_id="cmd_reply",
                related_task_id=task_id,
                reply=ReplyDraft(reply_id="reply_1", kind="answer", plain_text="done"),
                origin_message=_origin_message(),
            ),
        )
    )

    assert [event.event_type for event in outputs] == [EventType.OUTPUT_REPLY_READY]


def test_replied_for_terminal_task_emits_archive_command() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CREATE_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainCreateTaskPayload(command_id="cmd_1", request="ship it", origin_message=_origin_message()),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[2].payload.assignment_id

    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_STARTED,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskStartedReportPayload(task_id=task_id, agent_role="worker", assignment_id=assignment_id),
        )
    )
    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_RESULT,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskResultReportPayload(
                task_id=task_id,
                agent_role="worker",
                assignment_id=assignment_id,
                summary="done",
                result_text="done",
            ),
        )
    )

    outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.OUTPUT_REPLIED,
            source="delivery",
            target="broadcast",
            session_id="sess_1",
            task_id=task_id,
            correlation_id=task_id,
            payload=RepliedPayload(
                reply_id="reply_1",
                delivery_message=MessageRef(channel="cli", chat_id="direct", message_id="delivery_reply_1"),
                delivery_mode="chat",
                delivered_at="2026-03-15T00:00:00Z",
            ),
        )
    )

    assert len(outputs) == 1
    assert outputs[0].event_type == EventType.RUNTIME_ARCHIVE_TASK
    assert outputs[0].payload.archive_reason == "reply_delivered"


def test_delivery_failed_for_terminal_task_emits_archive_command() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.BRAIN_CREATE_TASK,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=BrainCreateTaskPayload(command_id="cmd_1", request="ship the change", origin_message=_origin_message()),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[2].payload.assignment_id

    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_STARTED,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskStartedReportPayload(task_id=task_id, agent_role="worker", assignment_id=assignment_id),
        )
    )
    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_REPORT_RESULT,
            source="worker",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskResultReportPayload(
                task_id=task_id,
                agent_role="worker",
                assignment_id=assignment_id,
                summary="done",
                result_text="done",
            ),
        )
    )

    outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.OUTPUT_DELIVERY_FAILED,
            source="delivery",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            correlation_id=task_id,
            payload=DeliveryFailedPayload(
                reply_id="reply_1",
                reason="missing_delivery_route",
                retryable=False,
            ),
        )
    )

    assert len(outputs) == 1
    assert outputs[0].event_type == EventType.RUNTIME_ARCHIVE_TASK
    assert outputs[0].payload.archive_reason == "delivery_failed"
