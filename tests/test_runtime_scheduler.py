from __future__ import annotations

from emoticorebot.protocol.commands import (
    TaskCancelPayload,
    TaskCreatePayload,
    TaskResumePayload,
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
from emoticorebot.protocol.task_models import InputRequest, MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.task.coordinator import RuntimeScheduler


def _origin_message() -> MessageRef:
    return MessageRef(channel="cli", chat_id="direct", message_id="msg_1")


def _create_payload(*, request: str, **context: object) -> TaskCreatePayload:
    payload_context = {"origin_message": _origin_message().model_dump(exclude_none=True)}
    payload_context.update(context)
    return TaskCreatePayload(command_id="cmd_1", request=request, context=payload_context)


def test_create_task_emits_update_then_assign_command() -> None:
    scheduler = RuntimeScheduler()
    command = build_envelope(
        event_type=EventType.TASK_CREATE,
        source="brain",
        target="runtime",
        session_id="sess_1",
        payload=TaskCreatePayload(
            command_id="cmd_1",
            request="fix the bug",
            context={
                "preferred_agent": "worker",
                "origin_message": _origin_message().model_dump(exclude_none=True),
            },
        ),
    )

    outputs = scheduler.dispatch(command)

    assert [event.event_type for event in outputs] == [
        EventType.TASK_UPDATE,
        EventType.RUNTIME_ASSIGN_AGENT,
    ]
    assert outputs[0].payload.message.startswith("开始处理")
    assert outputs[1].payload.agent_role == "worker"


def test_need_input_then_resume_emits_update_and_resume_command() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_CREATE,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=_create_payload(request="collect more info"),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[1].payload.assignment_id

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
    assert need_input[0].event_type == EventType.TASK_ASK
    assert need_input[0].payload.question == "Which city?"

    resumed = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_RESUME,
            source="brain",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskResumePayload(
                command_id="cmd_resume",
                task_id=task_id,
                state="running",
                user_input="Shanghai",
            ),
        )
    )

    assert [event.event_type for event in resumed] == [
        EventType.TASK_UPDATE,
        EventType.RUNTIME_RESUME_AGENT,
    ]


def test_resume_task_merges_memory_refs_and_skill_hints_into_request() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_CREATE,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=_create_payload(
                request="collect more info",
                memory_refs=["[workflow_pattern] 原始经验"],
                skill_hints=["技能 `old-skill` | 旧提示"],
            ),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[1].payload.assignment_id

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
    scheduler.dispatch(
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

    scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_RESUME,
            source="brain",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskResumePayload(
                command_id="cmd_resume",
                task_id=task_id,
                state="running",
                user_input="Shanghai",
                context={
                    "memory_refs": ["[workflow_pattern] 新经验"],
                    "skill_hints": ["技能 `new-skill` | 新提示"],
                },
            ),
        )
    )

    task = scheduler.get_task(task_id)

    assert task is not None
    assert task.request.memory_refs == ["[workflow_pattern] 原始经验", "[workflow_pattern] 新经验"]
    assert task.request.skill_hints == ["技能 `old-skill` | 旧提示", "技能 `new-skill` | 新提示"]


def test_review_flow_emits_summary_then_terminal_end() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_CREATE,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=_create_payload(request="write deployment plan", review_policy="required"),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[1].payload.assignment_id

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
        EventType.TASK_SUMMARY,
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

    assert [event.event_type for event in approved_outputs] == [EventType.TASK_END]
    assert approved_outputs[0].payload.output == "deployment plan"


def test_cancel_from_assigned_emits_end_and_cancel_agent() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_CREATE,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=_create_payload(request="stop me later"),
        )
    )
    task_id = create_outputs[0].task_id

    cancelled = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_CANCEL,
            source="brain",
            target="runtime",
            session_id="sess_1",
            task_id=task_id,
            payload=TaskCancelPayload(command_id="cmd_cancel", task_id=task_id, reason="user_requested"),
        )
    )

    assert [event.event_type for event in cancelled] == [
        EventType.TASK_END,
        EventType.RUNTIME_CANCEL_AGENT,
    ]
    assert cancelled[0].payload.result == "cancelled"


def test_replied_for_terminal_task_emits_archive_command() -> None:
    scheduler = RuntimeScheduler()
    create_outputs = scheduler.dispatch(
        build_envelope(
            event_type=EventType.TASK_CREATE,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=_create_payload(request="ship it"),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[1].payload.assignment_id

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
                delivery_mode="inline",
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
            event_type=EventType.TASK_CREATE,
            source="brain",
            target="runtime",
            session_id="sess_1",
            payload=_create_payload(request="ship the change"),
        )
    )
    task_id = create_outputs[0].task_id
    assignment_id = create_outputs[1].payload.assignment_id

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
