"""Runtime scheduler that owns task state transitions and event normalization."""

from __future__ import annotations

from typing import Any

from emoticorebot.protocol.commands import (
    BrainCancelTaskPayload,
    BrainCreateTaskPayload,
    BrainReplyPayload,
    BrainResumeTaskPayload,
)
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryFailedPayload,
    RepliedPayload,
    ReplyReadyPayload,
    SystemSignalPayload,
    TaskApprovedEventPayload,
    TaskApprovedReportPayload,
    TaskAssignedEventPayload,
    TaskCancelledEventPayload,
    TaskCreatedEventPayload,
    TaskFailedEventPayload,
    TaskFailedReportPayload,
    TaskNeedInputEventPayload,
    TaskNeedInputReportPayload,
    TaskPlanReadyReportPayload,
    TaskPlannedEventPayload,
    TaskProgressEventPayload,
    TaskProgressReportPayload,
    TaskRejectedEventPayload,
    TaskRejectedReportPayload,
    TaskResultEventPayload,
    TaskResultReportPayload,
    TaskReviewingEventPayload,
    TaskStartedEventPayload,
    TaskStartedReportPayload,
)
from emoticorebot.protocol.task_models import (
    AgentInputContext,
    MessageRef,
    ProvidedInputBundle,
    ProvidedInputItem,
    ReviewerContext,
    TaskRequestSpec,
)
from emoticorebot.protocol.task_result import TaskExecutionResult
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.state_machine import TaskStateMachine, TaskStatus

from .assignment import AssignmentFactory
from .recovery import RecoveryPlanner
from .task_store import RuntimeTaskRecord, TaskStore


class RuntimeScheduler:
    """Pure runtime decision core for task creation, normalization, and assignment."""

    def __init__(
        self,
        *,
        task_store: TaskStore | None = None,
        assignment_factory: AssignmentFactory | None = None,
        recovery: RecoveryPlanner | None = None,
    ) -> None:
        self._tasks = task_store or TaskStore()
        self._assignments = assignment_factory or AssignmentFactory()
        self._recovery = recovery or RecoveryPlanner(self._assignments)

    def dispatch(self, event: BusEnvelope[Any]) -> list[BusEnvelope[Any]]:
        event_type = str(event.event_type)
        routes = {
            EventType.BRAIN_CREATE_TASK: self._on_create_task,
            EventType.BRAIN_RESUME_TASK: self._on_resume_task,
            EventType.BRAIN_CANCEL_TASK: self._on_cancel_task,
            EventType.BRAIN_REPLY: self._on_reply_command,
            EventType.BRAIN_ASK_USER: self._on_reply_command,
            EventType.TASK_REPORT_STARTED: self._on_report_started,
            EventType.TASK_REPORT_PROGRESS: self._on_report_progress,
            EventType.TASK_REPORT_NEED_INPUT: self._on_report_need_input,
            EventType.TASK_REPORT_PLAN_READY: self._on_report_plan_ready,
            EventType.TASK_REPORT_RESULT: self._on_report_result,
            EventType.TASK_REPORT_APPROVED: self._on_report_approved,
            EventType.TASK_REPORT_REJECTED: self._on_report_rejected,
            EventType.TASK_REPORT_FAILED: self._on_report_failed,
            EventType.TASK_REPORT_CANCELLED: self._on_report_cancelled,
            EventType.RUNTIME_ARCHIVE_TASK: self._on_archive_task,
            EventType.SYSTEM_TIMEOUT: self._on_timeout,
            EventType.OUTPUT_REPLIED: self._on_replied,
            EventType.OUTPUT_DELIVERY_FAILED: self._on_delivery_failed,
        }
        try:
            handler = routes[event_type]
        except KeyError as exc:
            raise ValueError(f"unsupported scheduler event: {event_type}") from exc
        return handler(event)

    def get_task(self, task_id: str) -> RuntimeTaskRecord | None:
        return self._tasks.get(task_id)

    @property
    def task_store(self) -> TaskStore:
        return self._tasks

    def _on_create_task(self, event: BusEnvelope[BrainCreateTaskPayload]) -> list[BusEnvelope[Any]]:
        payload = event.payload
        task_id = self._assignments.new_task_id()
        request = TaskRequestSpec.model_validate(payload.model_dump(exclude={"command_id", "origin_message", "metadata"}))
        title = request.title or request.request[:48]
        task = RuntimeTaskRecord(
            task_id=task_id,
            session_id=event.session_id or "",
            turn_id=event.turn_id,
            request=request,
            origin_message=payload.origin_message,
            title=title,
            review_policy=request.review_policy or "skip",
            review_required=request.review_policy == "required",
            suppress_delivery=bool(payload.metadata.get("suppress_delivery")),
        )
        self._tasks.add(task)

        outputs: list[BusEnvelope[Any]] = [
            self._build_task_created(task=task, causation_id=event.event_id),
        ]

        task.status = TaskStateMachine.assign_agent(task.status)
        task.assignee = self._assignments.select_initial_role(task)
        task.current_assignment_id = self._assignments.new_assignment_id()
        task.touch()
        outputs.append(self._build_task_assigned(task=task, causation_id=event.event_id))
        outputs.append(
            self._assignments.build_assign_agent(task=task, agent_role=task.assignee, causation_id=event.event_id)
        )
        return outputs

    def _on_resume_task(self, event: BusEnvelope[BrainResumeTaskPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.suppress_delivery = task.suppress_delivery or bool(event.payload.metadata.get("suppress_delivery"))
        task.status = TaskStateMachine.resume_task(task.status)
        task.touch()

        resume_input = event.payload.provided_inputs or self._build_resume_input(event.payload)
        outputs: list[BusEnvelope[Any]] = [self._build_task_assigned(task=task, causation_id=event.event_id)]
        outputs.append(
            self._assignments.build_resume_agent(
                task=task,
                resume_input=resume_input,
                resume_message=event.payload.origin_message,
                causation_id=event.event_id,
            )
        )
        return outputs

    def _on_cancel_task(self, event: BusEnvelope[BrainCancelTaskPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.suppress_delivery = task.suppress_delivery or bool(event.payload.metadata.get("suppress_delivery"))
        task.status = TaskStateMachine.cancel_task(task.status)
        task.error = event.payload.reason or ""
        task.touch()

        outputs: list[BusEnvelope[Any]] = [
            self._build_task_cancelled(task=task, reason=event.payload.reason, causation_id=event.event_id)
        ]
        cancel_command = self._assignments.build_cancel_agent(task=task, reason=event.payload.reason, causation_id=event.event_id)
        if cancel_command is not None:
            outputs.append(cancel_command)
        return outputs

    def _on_reply_command(self, event: BusEnvelope[BrainReplyPayload]) -> list[BusEnvelope[Any]]:
        payload = ReplyReadyPayload(
            reply=event.payload.reply,
            origin_message=event.payload.origin_message,
            related_task_id=event.payload.related_task_id,
            related_event_id=event.causation_id,
        )
        outputs: list[BusEnvelope[Any]] = [
            build_envelope(
                event_type=EventType.OUTPUT_REPLY_READY,
                source="runtime",
                target="broadcast",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.payload.related_task_id,
                correlation_id=event.correlation_id or event.task_id,
                causation_id=event.event_id,
                payload=payload,
            )
        ]
        return outputs

    def _on_report_started(self, event: BusEnvelope[TaskStartedReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_started(task.status)
        task.assignee = event.payload.agent_role
        task.summary = event.payload.summary or task.summary
        task.touch()
        return [self._build_task_started(task=task, payload=event.payload, causation_id=event.event_id)]

    def _on_report_progress(self, event: BusEnvelope[TaskProgressReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_progress(task.status)
        task.summary = event.payload.summary or task.summary
        task.last_progress = event.payload.summary or event.payload.detail or ""
        task.touch()
        return [self._build_task_progress(task=task, payload=event.payload, causation_id=event.event_id)]

    def _on_report_need_input(self, event: BusEnvelope[TaskNeedInputReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_need_input(task.status)
        task.input_request = event.payload.input_request
        task.summary = event.payload.summary or task.summary
        task.touch()
        return [self._build_task_need_input(task=task, payload=event.payload, causation_id=event.event_id)]

    def _on_report_plan_ready(self, event: BusEnvelope[TaskPlanReadyReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_plan_ready(task.status)
        task.plan_id = event.payload.plan_id
        task.plan_steps = list(event.payload.steps)
        task.summary = event.payload.summary or task.summary
        task.touch()

        outputs: list[BusEnvelope[Any]] = [self._build_task_planned(task=task, payload=event.payload, causation_id=event.event_id)]
        task.status = TaskStateMachine.assign_agent(task.status)
        task.assignee = "worker"
        task.current_assignment_id = self._assignments.new_assignment_id()
        task.touch()
        outputs.append(self._build_task_assigned(task=task, causation_id=event.event_id))
        outputs.append(self._assignments.build_assign_agent(task=task, agent_role="worker", causation_id=event.event_id))
        return outputs

    def _on_report_result(self, event: BusEnvelope[TaskResultReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.latest_result = event.payload
        task.summary = event.payload.summary or task.summary

        review_required = bool(event.payload.reviewer_required or task.review_policy == "required")
        task.review_required = review_required
        next_status = TaskStateMachine.report_result(task.status, review_required=review_required)
        task.status = next_status

        if next_status is TaskStatus.DONE:
            task.touch()
            return [self._build_task_result(task=task, payload=event.payload, causation_id=event.event_id)]

        task.current_review_id = self._assignments.new_review_id()
        task.current_assignment_id = self._assignments.new_assignment_id()
        task.assignee = "reviewer"
        task.touch()
        reviewer_context = ReviewerContext(
            review_id=task.current_review_id,
            review_policy=task.review_policy,
            candidate_summary=event.payload.summary,
            candidate_result_text=event.payload.result_text,
            candidate_result_blocks=event.payload.result_blocks,
            candidate_artifacts=event.payload.artifacts,
            candidate_confidence=event.payload.confidence,
            acceptance_criteria=list(task.request.success_criteria),
        )
        return [
            self._build_task_reviewing(task=task, causation_id=event.event_id),
            self._assignments.build_assign_agent(
                task=task,
                agent_role="reviewer",
                reviewer_context=reviewer_context,
                causation_id=event.event_id,
            ),
        ]

    def _on_report_approved(self, event: BusEnvelope[TaskApprovedReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_approved(task.status)
        task.summary = event.payload.summary or task.summary
        task.touch()
        outputs: list[BusEnvelope[Any]] = [
            self._build_task_approved(task=task, payload=event.payload, causation_id=event.event_id)
        ]
        if task.latest_result is None:
            raise RuntimeError("review approved without a stored task result")
        outputs.append(self._build_task_result(task=task, payload=task.latest_result, causation_id=event.event_id))
        return outputs

    def _on_report_rejected(self, event: BusEnvelope[TaskRejectedReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_rejected(task.status)
        task.summary = event.payload.summary or task.summary
        task.latest_rejection_reason = event.payload.rejection_reason
        task.latest_findings = list(event.payload.findings)
        task.touch()

        task.current_assignment_id = self._assignments.new_assignment_id()
        task.assignee = "worker"
        reviewer_context = ReviewerContext(
            review_id=event.payload.review_id,
            review_policy=task.review_policy,
            prior_findings=event.payload.findings,
        )
        return [
            self._build_task_rejected(task=task, payload=event.payload, causation_id=event.event_id),
            self._build_task_assigned(task=task, causation_id=event.event_id),
            self._assignments.build_assign_agent(
                task=task,
                agent_role="worker",
                reviewer_context=reviewer_context,
                causation_id=event.event_id,
            ),
        ]

    def _on_report_failed(self, event: BusEnvelope[TaskFailedReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_failed(task.status)
        task.error = event.payload.reason or ""
        task.summary = event.payload.summary or ""
        task.touch()
        return [self._build_task_failed(task=task, payload=event.payload, causation_id=event.event_id)]

    def _on_report_cancelled(self, event: BusEnvelope[Any]) -> list[BusEnvelope[Any]]:
        del event
        return []

    def _on_archive_task(self, event: BusEnvelope[Any]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.archive_task(task.status)
        task.touch()
        return []

    def _on_timeout(self, event: BusEnvelope[SystemSignalPayload]) -> list[BusEnvelope[Any]]:
        task_id = event.payload.related_task_id or event.task_id
        if not task_id:
            return []
        task = self._tasks.require(task_id)
        task.status = TaskStateMachine.timeout_waiting_input(task.status)
        task.error = event.payload.reason or "waiting_input_timeout"
        task.summary = task.summary or "任务等待输入超时"
        task.touch()
        return [
            self._build_task_failed(
                task=task,
                payload=TaskFailedReportPayload(
                    task_id=task.task_id,
                    agent_role=task.assignee or "worker",
                    assignment_id=task.current_assignment_id or "",
                    reason=task.error,
                    summary=task.summary,
                    retryable=False,
                ),
                causation_id=event.event_id,
            )
        ]

    def _on_delivery_failed(self, event: BusEnvelope[DeliveryFailedPayload]) -> list[BusEnvelope[Any]]:
        if event.payload.retryable:
            return []
        task_id = event.task_id
        if not task_id:
            return []
        task = self._tasks.get(task_id)
        if task is None:
            return []
        return self._recovery.plan_archive(task, reason="delivery_failed")

    def _on_replied(self, event: BusEnvelope[RepliedPayload]) -> list[BusEnvelope[Any]]:
        task_id = event.task_id
        if not task_id:
            return []
        task = self._tasks.get(task_id)
        if task is None:
            return []
        return self._recovery.plan_archive(task, reason="reply_delivered")

    def _build_task_created(self, *, task: RuntimeTaskRecord, causation_id: str | None) -> BusEnvelope[TaskCreatedEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_CREATED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskCreatedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=task.summary or None,
                review_required=task.review_required,
                task_request=task.request,
                origin_message=task.origin_message or MessageRef(),
            ),
        )

    def _build_task_assigned(self, *, task: RuntimeTaskRecord, causation_id: str | None) -> BusEnvelope[TaskAssignedEventPayload]:
        if task.assignee is None or task.current_assignment_id is None:
            raise RuntimeError("task assignment requires assignee and assignment_id")
        return build_envelope(
            event_type=EventType.TASK_EVENT_ASSIGNED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskAssignedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=task.summary or None,
                assignee=task.assignee,
                review_required=task.review_required,
                assignment_id=task.current_assignment_id,
                agent_role=task.assignee,
            ),
        )

    def _build_task_started(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskStartedReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskStartedEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_STARTED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskStartedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=payload.agent_role,
                review_required=task.review_required,
                assignment_id=payload.assignment_id,
                agent_role=payload.agent_role,
                started_at=payload.started_at,
            ),
        )

    def _build_task_progress(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskProgressReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskProgressEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_PROGRESS,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskProgressEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=task.review_required,
                progress=payload.progress,
                detail=payload.detail,
                current_step_id=payload.current_step_id,
                next_step=payload.next_step,
                metadata=dict(payload.metadata),
            ),
        )

    def _build_task_need_input(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskNeedInputReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskNeedInputEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_NEED_INPUT,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskNeedInputEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=task.review_required,
                input_request=payload.input_request,
                partial_result=payload.partial_result,
            ),
        )

    def _build_task_planned(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskPlanReadyReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskPlannedEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_PLANNED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskPlannedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=task.review_required,
                plan_id=payload.plan_id,
                steps=payload.steps,
            ),
        )

    def _build_task_reviewing(self, *, task: RuntimeTaskRecord, causation_id: str | None) -> BusEnvelope[TaskReviewingEventPayload]:
        if task.current_review_id is None:
            raise RuntimeError("reviewing tasks require review_id")
        return build_envelope(
            event_type=EventType.TASK_EVENT_REVIEWING,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskReviewingEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=task.summary or None,
                assignee=task.assignee,
                review_required=True,
                review_id=task.current_review_id,
                reviewer_role="reviewer",
            ),
        )

    def _build_task_approved(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskApprovedReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskApprovedEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_APPROVED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskApprovedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=False,
                review_id=payload.review_id,
                notes=payload.notes,
            ),
        )

    def _build_task_rejected(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskRejectedReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskRejectedEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_REJECTED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskRejectedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=True,
                review_id=payload.review_id,
                rejection_reason=payload.rejection_reason,
                findings=payload.findings,
            ),
        )

    def _build_task_result(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskResultReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskResultEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_RESULT,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskResultEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=task.review_required,
                result_text=payload.result_text,
                result_blocks=payload.result_blocks,
                artifacts=payload.artifacts,
                confidence=payload.confidence,
            ),
        )

    def _build_task_failed(
        self,
        *,
        task: RuntimeTaskRecord,
        payload: TaskFailedReportPayload,
        causation_id: str | None,
    ) -> BusEnvelope[TaskFailedEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_FAILED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskFailedEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=payload.summary,
                assignee=task.assignee,
                review_required=task.review_required,
                reason=payload.reason,
                retryable=payload.retryable,
            ),
        )

    def _build_task_cancelled(
        self,
        *,
        task: RuntimeTaskRecord,
        reason: str | None,
        causation_id: str | None,
    ) -> BusEnvelope[TaskCancelledEventPayload]:
        return build_envelope(
            event_type=EventType.TASK_EVENT_CANCELLED,
            source="runtime",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskCancelledEventPayload(
                task_id=task.task_id,
                state=task.snapshot(),
                summary=task.summary or None,
                assignee=task.assignee,
                review_required=task.review_required,
                reason=reason,
                cancelled_by="brain",
            ),
        )

    @staticmethod
    def _build_resume_input(payload: BrainResumeTaskPayload) -> ProvidedInputBundle:
        items: list[ProvidedInputItem] = []
        if payload.user_input:
            items.append(
                ProvidedInputItem(
                    field="user_input",
                    value_text=payload.user_input,
                    source="user_message",
                )
            )
        return ProvidedInputBundle(
            plain_text=payload.user_input,
            items=items,
            source_message=payload.origin_message,
        )


__all__ = ["RuntimeScheduler"]
