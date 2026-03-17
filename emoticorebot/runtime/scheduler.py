"""Runtime scheduler that normalizes internal task reports into the compact task protocol."""

from __future__ import annotations

from typing import Any

from emoticorebot.protocol.commands import BrainReplyPayload, TaskCancelPayload, TaskCreatePayload, TaskResumePayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    DeliveryFailedPayload,
    RepliedPayload,
    ReplyReadyPayload,
    SystemSignalPayload,
    TaskApprovedReportPayload,
    TaskAskPayload,
    TaskCancelledReportPayload,
    TaskEndPayload,
    TaskFailedReportPayload,
    TaskNeedInputReportPayload,
    TaskPlanReadyReportPayload,
    TaskProgressReportPayload,
    TaskRejectedReportPayload,
    TaskResultReportPayload,
    TaskStartedReportPayload,
    TaskSummaryPayload,
    TaskUpdatePayload,
)
from emoticorebot.protocol.task_models import MessageRef, ProvidedInputBundle, ProvidedInputItem, ReviewerContext, TaskRequestSpec
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
        routes = {
            EventType.TASK_CREATE: self._on_create_task,
            EventType.TASK_RESUME: self._on_resume_task,
            EventType.TASK_CANCEL: self._on_cancel_task,
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
            handler = routes[str(event.event_type)]
        except KeyError as exc:
            raise ValueError(f"unsupported scheduler event: {event.event_type}") from exc
        return handler(event)

    def get_task(self, task_id: str) -> RuntimeTaskRecord | None:
        return self._tasks.get(task_id)

    @property
    def task_store(self) -> TaskStore:
        return self._tasks

    def _on_create_task(self, event: BusEnvelope[TaskCreatePayload]) -> list[BusEnvelope[Any]]:
        payload = event.payload
        task_id = self._assignments.new_task_id()
        request = self._build_task_request(payload)
        title = request.title or request.request[:48]
        task = RuntimeTaskRecord(
            task_id=task_id,
            session_id=event.session_id or "",
            turn_id=event.turn_id,
            request=request,
            origin_message=self._origin_message(payload.context),
            title=title,
            review_policy=request.review_policy or "skip",
            review_required=request.review_policy == "required",
            suppress_delivery=bool(payload.context.get("suppress_delivery")),
        )
        self._tasks.add(task)

        task.status = TaskStateMachine.assign_agent(task.status)
        task.assignee = self._assignments.select_initial_role(task)
        task.current_assignment_id = self._assignments.new_assignment_id()
        task.summary = str(payload.message or f"开始处理 {task.title}。").strip()
        task.touch()
        return [
            self._build_task_update(
                task=task,
                message=task.summary,
                stage="dispatch",
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="info",
                        message=task.summary,
                        data={"stage": "dispatch"},
                    )
                ],
                causation_id=event.event_id,
            ),
            self._assignments.build_assign_agent(task=task, agent_role=task.assignee, causation_id=event.event_id),
        ]

    def _on_resume_task(self, event: BusEnvelope[TaskResumePayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.resume_task(task.status)
        task.input_request = None
        task.summary = str(event.payload.message or task.summary or "收到补充信息，继续处理。").strip()
        task.touch()

        resume_input = event.payload.provided_inputs or self._build_resume_input(event.payload)
        return [
            self._build_task_update(
                task=task,
                message=task.summary,
                stage="resume",
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="info",
                        message=task.summary,
                        data={"stage": "resume"},
                    )
                ],
                causation_id=event.event_id,
            ),
            self._assignments.build_resume_agent(
                task=task,
                resume_input=resume_input,
                resume_message=resume_input.source_message,
                causation_id=event.event_id,
            ),
        ]

    def _on_cancel_task(self, event: BusEnvelope[TaskCancelPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.cancel_task(task.status)
        task.error = str(event.payload.reason or "").strip()
        task.summary = task.summary or "任务已取消。"
        task.touch()

        outputs: list[BusEnvelope[Any]] = [
            self._build_task_end(
                task=task,
                result="cancelled",
                summary=task.summary,
                error=task.error or None,
                trace_final=[
                    self._trace_item(
                        task=task,
                        kind="warning",
                        message=task.summary,
                        data=self._compact_dict({"result": "cancelled", "reason": task.error or None}),
                    )
                ],
                causation_id=event.event_id,
            )
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
        return [
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

    def _on_report_started(self, event: BusEnvelope[TaskStartedReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_started(task.status)
        task.assignee = event.payload.agent_role
        task.summary = str(event.payload.summary or task.summary or "任务开始执行。").strip()
        task.touch()
        return [
            self._build_task_update(
                task=task,
                message=task.summary,
                stage="run",
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="info",
                        message=task.summary,
                        data=self._compact_dict(
                            {
                                "stage": "run",
                                "agent_role": event.payload.agent_role,
                                "assignment_id": event.payload.assignment_id,
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

    def _on_report_progress(self, event: BusEnvelope[TaskProgressReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_progress(task.status)
        task.summary = str(event.payload.summary or task.summary).strip()
        task.last_progress = str(event.payload.summary or event.payload.detail or "").strip()
        task.touch()
        message = task.last_progress or "任务状态已更新。"
        return [
            self._build_task_update(
                task=task,
                message=message,
                progress=event.payload.progress,
                stage=event.payload.current_step_id,
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind=("tool" if str(event.payload.metadata.get("tool_name", "") or "").strip() else "progress"),
                        message=message,
                        data=self._compact_dict(
                            {
                                "progress": event.payload.progress,
                                "stage": event.payload.current_step_id,
                                "next_step": event.payload.next_step,
                                "tool_name": event.payload.metadata.get("tool_name"),
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

    def _on_report_need_input(self, event: BusEnvelope[TaskNeedInputReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_need_input(task.status)
        task.input_request = event.payload.input_request
        task.summary = str(event.payload.summary or task.summary).strip()
        task.touch()
        question = str(event.payload.input_request.question or "请补充继续执行所需的信息。").strip()
        why = str(event.payload.summary or event.payload.partial_result or "").strip() or None
        return [
            self._build_task_ask(
                task=task,
                question=question,
                field=event.payload.input_request.field,
                why=why,
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="ask",
                        message=question,
                        data=self._compact_dict(
                            {
                                "field": event.payload.input_request.field,
                                "why": why,
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

    def _on_report_plan_ready(self, event: BusEnvelope[TaskPlanReadyReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_plan_ready(task.status)
        task.plan_id = event.payload.plan_id
        task.plan_steps = list(event.payload.steps)
        task.summary = str(event.payload.summary or task.summary or "任务规划已完成。").strip()
        task.touch()

        outputs: list[BusEnvelope[Any]] = [
            self._build_task_summary(
                task=task,
                summary=task.summary,
                stage="plan",
                next_step=event.payload.reviewer_hint or "进入执行",
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="summary",
                        message=task.summary,
                        data=self._compact_dict(
                            {
                                "stage": "plan",
                                "plan_id": event.payload.plan_id,
                                "next_step": event.payload.reviewer_hint or "进入执行",
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            )
        ]
        task.status = TaskStateMachine.assign_agent(task.status)
        task.assignee = "worker"
        task.current_assignment_id = self._assignments.new_assignment_id()
        task.touch()
        outputs.append(self._assignments.build_assign_agent(task=task, agent_role="worker", causation_id=event.event_id))
        return outputs

    def _on_report_result(self, event: BusEnvelope[TaskResultReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.latest_result = event.payload
        task.summary = str(event.payload.summary or task.summary).strip()

        review_required = bool(event.payload.reviewer_required or task.review_policy == "required")
        task.review_required = review_required
        task.status = TaskStateMachine.report_result(task.status, review_required=review_required)
        task.touch()

        if task.status is TaskStatus.DONE:
            return [
                self._build_task_end(
                    task=task,
                    result="success",
                    summary=event.payload.summary,
                    output=event.payload.result_text,
                    trace_final=[
                        self._trace_item(
                            task=task,
                            kind="summary",
                            message=str(event.payload.summary or event.payload.result_text or "任务已完成。").strip(),
                            data=self._compact_dict(
                                {
                                    "result": "success",
                                    "output": event.payload.result_text,
                                    "confidence": event.payload.confidence,
                                }
                            ),
                        )
                    ],
                    causation_id=event.event_id,
                )
            ]

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
            self._build_task_summary(
                task=task,
                summary=str(event.payload.summary or "结果已生成，进入审核。").strip(),
                stage="review",
                next_step="等待审核",
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="summary",
                        message=str(event.payload.summary or "结果已生成，进入审核。").strip(),
                        data=self._compact_dict(
                            {
                                "stage": "review",
                                "next_step": "等待审核",
                                "confidence": event.payload.confidence,
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            ),
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
        task.summary = str(event.payload.summary or task.summary).strip()
        task.touch()
        if task.latest_result is None:
            raise RuntimeError("review approved without a stored task result")
        return [
            self._build_task_end(
                task=task,
                result="success",
                summary=task.latest_result.summary or task.summary,
                output=task.latest_result.result_text,
                trace_final=[
                    self._trace_item(
                        task=task,
                        kind="summary",
                        message=str(task.latest_result.summary or task.latest_result.result_text or "任务已完成。").strip(),
                        data=self._compact_dict(
                            {
                                "result": "success",
                                "output": task.latest_result.result_text,
                                "confidence": task.latest_result.confidence,
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

    def _on_report_rejected(self, event: BusEnvelope[TaskRejectedReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        task.status = TaskStateMachine.report_rejected(task.status)
        task.summary = str(event.payload.summary or task.summary).strip()
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
            self._build_task_summary(
                task=task,
                summary=str(event.payload.summary or event.payload.rejection_reason or "审核未通过，返回执行。").strip(),
                stage="review",
                next_step="重新执行",
                trace_append=[
                    self._trace_item(
                        task=task,
                        kind="summary",
                        message=str(event.payload.summary or event.payload.rejection_reason or "审核未通过，返回执行。").strip(),
                        data=self._compact_dict(
                            {
                                "stage": "review",
                                "next_step": "重新执行",
                                "rejection_reason": event.payload.rejection_reason,
                            }
                        ),
                    )
                ],
                causation_id=event.event_id,
            ),
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
        task.error = str(event.payload.reason or "").strip()
        task.summary = str(event.payload.summary or task.summary or task.error or "任务执行失败。").strip()
        task.touch()
        return [
            self._build_task_end(
                task=task,
                result="failed",
                summary=task.summary,
                error=task.error or None,
                trace_final=[
                    self._trace_item(
                        task=task,
                        kind="error",
                        message=task.summary,
                        data=self._compact_dict({"result": "failed", "error": task.error or None}),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

    def _on_report_cancelled(self, event: BusEnvelope[TaskCancelledReportPayload]) -> list[BusEnvelope[Any]]:
        task = self._tasks.require(event.payload.task_id)
        if task.status is TaskStatus.CANCELLED:
            return []
        task.status = TaskStatus.CANCELLED
        task.error = str(event.payload.reason or task.error).strip()
        task.summary = task.summary or "任务已取消。"
        task.touch()
        return [
            self._build_task_end(
                task=task,
                result="cancelled",
                summary=task.summary,
                error=task.error or None,
                trace_final=[
                    self._trace_item(
                        task=task,
                        kind="warning",
                        message=task.summary,
                        data=self._compact_dict({"result": "cancelled", "reason": task.error or None}),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

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
        task.summary = task.summary or "任务等待输入超时。"
        task.touch()
        return [
            self._build_task_end(
                task=task,
                result="failed",
                summary=task.summary,
                error=task.error,
                trace_final=[
                    self._trace_item(
                        task=task,
                        kind="error",
                        message=task.summary,
                        data=self._compact_dict({"result": "failed", "error": task.error}),
                    )
                ],
                causation_id=event.event_id,
            )
        ]

    def _on_delivery_failed(self, event: BusEnvelope[DeliveryFailedPayload]) -> list[BusEnvelope[Any]]:
        if event.payload.retryable:
            return []
        if not event.task_id:
            return []
        task = self._tasks.get(event.task_id)
        if task is None:
            return []
        return self._recovery.plan_archive(task, reason="delivery_failed")

    def _on_replied(self, event: BusEnvelope[RepliedPayload]) -> list[BusEnvelope[Any]]:
        if not event.task_id:
            return []
        task = self._tasks.get(event.task_id)
        if task is None:
            return []
        return self._recovery.plan_archive(task, reason="reply_delivered")

    def _build_task_update(
        self,
        *,
        task: RuntimeTaskRecord,
        message: str,
        stage: str | None = None,
        progress: float | None = None,
        trace_append: list[dict[str, Any]] | None = None,
        causation_id: str | None,
    ) -> BusEnvelope[TaskUpdatePayload]:
        return build_envelope(
            event_type=EventType.TASK_UPDATE,
            source="task",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskUpdatePayload(
                task_id=task.task_id,
                state="running",
                result="none",
                updated_at=task.updated_at,
                message=str(message or "任务状态已更新。").strip(),
                progress=progress,
                stage=stage,
                trace_append=list(trace_append or []),
            ),
        )

    def _build_task_summary(
        self,
        *,
        task: RuntimeTaskRecord,
        summary: str,
        stage: str | None = None,
        next_step: str | None = None,
        trace_append: list[dict[str, Any]] | None = None,
        causation_id: str | None,
    ) -> BusEnvelope[TaskSummaryPayload]:
        return build_envelope(
            event_type=EventType.TASK_SUMMARY,
            source="task",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskSummaryPayload(
                task_id=task.task_id,
                state="running",
                result="none",
                updated_at=task.updated_at,
                summary=str(summary or "任务阶段总结已更新。").strip(),
                stage=stage,
                next_step=next_step,
                trace_append=list(trace_append or []),
            ),
        )

    def _build_task_ask(
        self,
        *,
        task: RuntimeTaskRecord,
        question: str,
        field: str | None = None,
        why: str | None = None,
        trace_append: list[dict[str, Any]] | None = None,
        causation_id: str | None,
    ) -> BusEnvelope[TaskAskPayload]:
        return build_envelope(
            event_type=EventType.TASK_ASK,
            source="task",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskAskPayload(
                task_id=task.task_id,
                state="waiting",
                result="none",
                updated_at=task.updated_at,
                question=str(question or "请补充继续执行所需的信息。").strip(),
                field=str(field or "").strip() or None,
                why=str(why or "").strip() or None,
                trace_append=list(trace_append or []),
            ),
        )

    def _build_task_end(
        self,
        *,
        task: RuntimeTaskRecord,
        result: str,
        summary: str | None = None,
        output: str | None = None,
        error: str | None = None,
        trace_final: list[dict[str, Any]] | None = None,
        causation_id: str | None,
    ) -> BusEnvelope[TaskEndPayload]:
        return build_envelope(
            event_type=EventType.TASK_END,
            source="task",
            target="broadcast",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=TaskEndPayload(
                task_id=task.task_id,
                state="done",
                result=result,
                updated_at=task.updated_at,
                summary=str(summary or task.summary or "").strip() or None,
                output=str(output or "").strip() or None,
                error=str(error or "").strip() or None,
                trace_final=list(trace_final or []),
            ),
        )

    @staticmethod
    def _build_resume_input(payload: TaskResumePayload) -> ProvidedInputBundle:
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
            source_message=None,
        )

    @staticmethod
    def _build_task_request(payload: TaskCreatePayload) -> TaskRequestSpec:
        context = dict(payload.context or {})
        review_policy = str(context.get("review_policy", "") or "").strip() or None
        preferred_agent = str(context.get("preferred_agent", "") or "").strip() or None
        if review_policy not in {"skip", "optional", "required"}:
            review_policy = None
        if preferred_agent not in {"planner", "worker"}:
            preferred_agent = None
        return TaskRequestSpec(
            request=payload.request,
            title=str(context.get("title", "") or "").strip() or None,
            goal=payload.goal,
            expected_output=str(context.get("expected_output", "") or "").strip() or None,
            constraints=RuntimeScheduler._string_list(context.get("constraints")),
            success_criteria=RuntimeScheduler._string_list(context.get("success_criteria")),
            history_context=str(context.get("history_context", "") or "").strip() or None,
            content_blocks=list(context.get("content_blocks", []) or []),
            memory_refs=RuntimeScheduler._string_list(context.get("memory_refs")),
            skill_hints=RuntimeScheduler._string_list(context.get("skill_hints")),
            review_policy=review_policy,
            preferred_agent=preferred_agent,
        )

    @staticmethod
    def _origin_message(context: dict[str, Any]) -> MessageRef | None:
        raw_origin = context.get("origin_message")
        if raw_origin is None:
            return None
        if isinstance(raw_origin, MessageRef):
            return raw_origin
        if isinstance(raw_origin, dict):
            return MessageRef.model_validate(raw_origin)
        return None

    @staticmethod
    def _trace_item(
        *,
        task: RuntimeTaskRecord,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "trace_id": f"{task.task_id}:{task.state_version}:{kind}",
            "task_id": task.task_id,
            "session_id": task.session_id,
            "ts": task.updated_at,
            "kind": kind,
            "message": str(message or "").strip(),
            "data": dict(data or {}),
        }

    @staticmethod
    def _compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if value not in ("", None, [], {})}

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in (str(entry or "") for entry in value) if item.strip()]


__all__ = ["RuntimeScheduler"]
