"""Planner / worker / reviewer agents for the bus-driven runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.execution.deep_agent_executor import DeepAgentExecutor
from emoticorebot.protocol.commands import AssignAgentPayload, ResumeAgentPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import (
    TaskApprovedReportPayload,
    TaskFailedReportPayload,
    TaskNeedInputReportPayload,
    TaskPlanReadyReportPayload,
    TaskProgressReportPayload,
    TaskRejectedReportPayload,
    TaskResultReportPayload,
    TaskStartedReportPayload,
)
from emoticorebot.protocol.task_models import InputRequest, PlanStep, ReviewItem
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.task_store import RuntimeTaskRecord, TaskStore


@dataclass(slots=True)
class WorkerOutcome:
    status: str
    summary: str
    result_text: str | None = None
    input_request: InputRequest | None = None
    partial_result: str | None = None
    confidence: float | None = None
    reviewer_required: bool | None = None


class AgentTeam:
    """Registers planner, worker, and reviewer roles on the shared bus."""

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        task_store: TaskStore,
        worker_llm: Any | None = None,
        context_builder: Any | None = None,
        tool_registry: Any | None = None,
    ) -> None:
        self._bus = bus
        self._task_store = task_store
        self._planner = PlannerAgent(bus)
        self._worker = WorkerAgent(
            bus,
            task_store,
            worker_llm=worker_llm,
            context_builder=context_builder,
            tool_registry=tool_registry,
        )
        self._reviewer = ReviewerAgent(bus)

    def register(self) -> None:
        self._planner.register()
        self._worker.register()
        self._reviewer.register()

    async def stop(self) -> None:
        await self._planner.stop()
        await self._worker.stop()
        await self._reviewer.stop()


class _AsyncAgent:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Any, *, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


class PlannerAgent(_AsyncAgent):
    def __init__(self, bus: PriorityPubSubBus) -> None:
        super().__init__()
        self._bus = bus

    def register(self) -> None:
        self._bus.subscribe(consumer="planner", event_type=EventType.RUNTIME_ASSIGN_AGENT, handler=self._handle_assign)

    async def _handle_assign(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        if event.target != "planner":
            return
        self._spawn(self._run(event), name=f"planner:{event.payload.task_id}")

    async def _run(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        request = event.payload.task_request
        if request is None:
            await self._publish_failed(event, reason="missing_task_request", summary="planner 未收到 task_request")
            return
        await self._publish_started(event)
        title = request.title or request.request[:32]
        steps = [
            PlanStep(step_id="step_understand", title="理解目标", description=f"澄清 {title} 的完成标准", role="planner"),
            PlanStep(step_id="step_execute", title="执行主任务", description="交由 worker 实施计划", role="worker"),
        ]
        if request.review_policy == "required":
            steps.append(PlanStep(step_id="step_review", title="结果审核", description="由 reviewer 审核最终产出", role="reviewer"))
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_PLAN_READY,
                source="planner",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.payload.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=TaskPlanReadyReportPayload(
                    task_id=event.payload.task_id,
                    assignment_id=event.payload.assignment_id,
                    plan_id=f"plan_{event.payload.task_id}",
                    summary="执行计划已准备好",
                    steps=steps,
                    reviewer_hint="重点检查产出是否满足任务目标",
                ),
            )
        )

    async def _publish_started(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_STARTED,
                source="planner",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.payload.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=TaskStartedReportPayload(
                    task_id=event.payload.task_id,
                    agent_role="planner",
                    assignment_id=event.payload.assignment_id,
                    summary="planner 已接手任务",
                ),
            )
        )

    async def _publish_failed(self, event: BusEnvelope[AssignAgentPayload], *, reason: str, summary: str) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_FAILED,
                source="planner",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.payload.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=TaskFailedReportPayload(
                    task_id=event.payload.task_id,
                    agent_role="planner",
                    assignment_id=event.payload.assignment_id,
                    reason=reason,
                    summary=summary,
                    retryable=False,
                ),
            )
        )


class WorkerAgent(_AsyncAgent):
    def __init__(
        self,
        bus: PriorityPubSubBus,
        task_store: TaskStore,
        *,
        worker_llm: Any | None,
        context_builder: Any | None,
        tool_registry: Any | None,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._task_store = task_store
        self._worker_llm = worker_llm
        self._context_builder = context_builder
        self._tool_registry = tool_registry
        self._executor: DeepAgentExecutor | None = None

    def register(self) -> None:
        self._bus.subscribe(consumer="worker", event_type=EventType.RUNTIME_ASSIGN_AGENT, handler=self._handle_assign)
        self._bus.subscribe(consumer="worker", event_type=EventType.RUNTIME_RESUME_AGENT, handler=self._handle_resume)

    async def _handle_assign(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        if event.target != "worker":
            return
        self._spawn(self._run_assign(event), name=f"worker:{event.payload.task_id}:assign")

    async def _handle_resume(self, event: BusEnvelope[ResumeAgentPayload]) -> None:
        if event.target != "worker":
            return
        self._spawn(self._run_resume(event), name=f"worker:{event.payload.task_id}:resume")

    async def _run_assign(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        task = self._task_store.require(event.payload.task_id)
        await self._publish_started(
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.payload.task_id,
            assignment_id=event.payload.assignment_id,
            causation_id=event.event_id,
        )
        outcome = await self._execute(
            task=task,
            assignment_id=event.payload.assignment_id,
            session_id=event.session_id,
            turn_id=event.turn_id,
            correlation_id=event.correlation_id,
        )
        await self._publish_outcome(
            outcome=outcome,
            task=task,
            assignment_id=event.payload.assignment_id,
            session_id=event.session_id,
            turn_id=event.turn_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
        )

    async def _run_resume(self, event: BusEnvelope[ResumeAgentPayload]) -> None:
        task = self._task_store.require(event.payload.task_id)
        await self._publish_started(
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.payload.task_id,
            assignment_id=event.payload.assignment_id,
            causation_id=event.event_id,
        )
        outcome = await self._execute(
            task=task,
            assignment_id=event.payload.assignment_id,
            session_id=event.session_id,
            turn_id=event.turn_id,
            correlation_id=event.correlation_id,
            resume_input=event.payload.resume_input,
        )
        await self._publish_outcome(
            outcome=outcome,
            task=task,
            assignment_id=event.payload.assignment_id,
            session_id=event.session_id,
            turn_id=event.turn_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
        )

    async def _execute(
        self,
        *,
        task: RuntimeTaskRecord,
        assignment_id: str,
        session_id: str | None,
        turn_id: str | None,
        correlation_id: str | None,
        resume_input: Any | None = None,
    ) -> WorkerOutcome:
        if self._context_builder is None:
            return WorkerOutcome(
                status="result",
                summary="worker 已记录任务",
                result_text=f"已接收任务：{task.request.request}",
                confidence=0.6,
                reviewer_required=task.review_policy == "required",
            )

        executor = self._get_executor()
        task_spec = self._build_task_spec(task, session_id=session_id, resume_input=resume_input)
        raw = await executor.execute(
            task_spec,
            task_id=task.task_id,
            progress_reporter=self._progress_reporter(
                session_id=session_id,
                turn_id=turn_id,
                task_id=task.task_id,
                assignment_id=assignment_id,
                correlation_id=correlation_id,
            ),
        )
        return self._normalize_outcome(task, raw)

    def _get_executor(self) -> DeepAgentExecutor:
        if self._executor is None:
            self._executor = DeepAgentExecutor(self._worker_llm, self._tool_registry, self._context_builder)
        return self._executor

    @staticmethod
    def _build_task_spec(
        task: RuntimeTaskRecord,
        *,
        session_id: str | None,
        resume_input: Any | None,
    ) -> dict[str, Any]:
        task_context: dict[str, Any] = {}
        if resume_input is not None:
            provided_inputs: dict[str, str] = {}
            if getattr(resume_input, "plain_text", None):
                provided_inputs["user_input"] = str(resume_input.plain_text)
            for item in getattr(resume_input, "items", []):
                if item.field and item.value_text:
                    provided_inputs[str(item.field)] = str(item.value_text)
            if provided_inputs:
                task_context["provided_inputs"] = provided_inputs

        return {
            "task_id": task.task_id,
            "title": task.title,
            "request": task.request.request,
            "goal": task.request.goal or "",
            "expected_output": task.request.expected_output or "",
            "history_context": task.request.history_context or "",
            "constraints": list(task.request.constraints),
            "success_criteria": list(task.request.success_criteria),
            "skill_hints": list(task.request.skill_hints),
            "session_id": str(session_id or task.session_id or "").strip(),
            "task_context": task_context,
        }

    @staticmethod
    def _normalize_outcome(task: RuntimeTaskRecord, raw: Any) -> WorkerOutcome:
        if not isinstance(raw, dict):
            return WorkerOutcome(status="failed", summary="worker 未收到有效结果")
        control_state = str(raw.get("control_state", "completed") or "completed").strip()
        summary = str(raw.get("message", "") or raw.get("analysis", "") or "").strip() or "worker 完成了任务"
        if control_state == "waiting_input":
            missing = [str(item).strip() for item in list(raw.get("missing", []) or []) if str(item).strip()]
            question = str(raw.get("recommended_action", "") or "").strip()
            if not question:
                question = f"请补充：{missing[0]}" if missing else "请补充继续执行所需的信息。"
            return WorkerOutcome(
                status="need_input",
                summary=summary,
                input_request=InputRequest(
                    field=missing[0] if missing else "user_input",
                    question=question,
                    required=True,
                    expected_type="text",
                ),
                partial_result=summary,
                confidence=float(raw.get("confidence", 0.5) or 0.5),
            )
        if control_state == "failed":
            return WorkerOutcome(status="failed", summary=summary)
        return WorkerOutcome(
            status="result",
            summary=summary,
            result_text=str(raw.get("message", "") or summary).strip(),
            confidence=float(raw.get("confidence", 0.8) or 0.8),
            reviewer_required=task.review_policy == "required",
        )

    def _progress_reporter(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        task_id: str,
        assignment_id: str,
        correlation_id: str | None,
    ):
        async def _report(message: str, payload: dict[str, Any]) -> None:
            nested = payload.get("payload")
            extra = nested if isinstance(nested, dict) else {}
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.TASK_REPORT_PROGRESS,
                    source="worker",
                    target="runtime",
                    session_id=session_id,
                    turn_id=turn_id,
                    task_id=task_id,
                    correlation_id=correlation_id or task_id,
                    payload=TaskProgressReportPayload(
                        task_id=task_id,
                        agent_role="worker",
                        assignment_id=assignment_id,
                        summary=str(message or "").strip(),
                        detail=str(payload.get("phase", "") or "").strip() or None,
                        progress=extra.get("progress"),
                        current_step_id=str(extra.get("current_step_id", "") or payload.get("tool_name", "") or "").strip()
                        or None,
                        next_step=str(extra.get("next_step", "") or "").strip() or None,
                        metadata={
                            "event": str(payload.get("event", "") or "").strip(),
                            "producer": str(payload.get("producer", "") or "").strip(),
                            "tool_name": str(payload.get("tool_name", "") or "").strip(),
                        },
                    ),
                )
            )

        return _report

    async def _publish_started(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        task_id: str,
        assignment_id: str,
        causation_id: str,
    ) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_STARTED,
                source="worker",
                target="runtime",
                session_id=session_id,
                turn_id=turn_id,
                task_id=task_id,
                correlation_id=task_id,
                causation_id=causation_id,
                payload=TaskStartedReportPayload(
                    task_id=task_id,
                    agent_role="worker",
                    assignment_id=assignment_id,
                    summary="worker 已开始执行",
                ),
            )
        )

    async def _publish_outcome(
        self,
        *,
        outcome: WorkerOutcome,
        task: RuntimeTaskRecord,
        assignment_id: str,
        session_id: str | None,
        turn_id: str | None,
        correlation_id: str | None,
        causation_id: str,
    ) -> None:
        if outcome.status == "need_input" and outcome.input_request is not None:
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.TASK_REPORT_NEED_INPUT,
                    source="worker",
                    target="runtime",
                    session_id=session_id,
                    turn_id=turn_id,
                    task_id=task.task_id,
                    correlation_id=correlation_id or task.task_id,
                    causation_id=causation_id,
                    payload=TaskNeedInputReportPayload(
                        task_id=task.task_id,
                        agent_role="worker",
                        assignment_id=assignment_id,
                        input_request=outcome.input_request,
                        summary=outcome.summary,
                        partial_result=outcome.partial_result,
                    ),
                )
            )
            return
        if outcome.status == "failed":
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.TASK_REPORT_FAILED,
                    source="worker",
                    target="runtime",
                    session_id=session_id,
                    turn_id=turn_id,
                    task_id=task.task_id,
                    correlation_id=correlation_id or task.task_id,
                    causation_id=causation_id,
                    payload=TaskFailedReportPayload(
                        task_id=task.task_id,
                        agent_role="worker",
                        assignment_id=assignment_id,
                        reason=outcome.summary,
                        summary=outcome.summary,
                        retryable=False,
                    ),
                )
            )
            return
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_RESULT,
                source="worker",
                target="runtime",
                session_id=session_id,
                turn_id=turn_id,
                task_id=task.task_id,
                correlation_id=correlation_id or task.task_id,
                causation_id=causation_id,
                payload=TaskResultReportPayload(
                    task_id=task.task_id,
                    agent_role="worker",
                    assignment_id=assignment_id,
                    summary=outcome.summary,
                    result_text=outcome.result_text,
                    confidence=outcome.confidence,
                    reviewer_required=outcome.reviewer_required,
                ),
            )
        )


class ReviewerAgent(_AsyncAgent):
    def __init__(self, bus: PriorityPubSubBus) -> None:
        super().__init__()
        self._bus = bus

    def register(self) -> None:
        self._bus.subscribe(consumer="reviewer", event_type=EventType.RUNTIME_ASSIGN_AGENT, handler=self._handle_assign)

    async def _handle_assign(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        if event.target != "reviewer":
            return
        self._spawn(self._run(event), name=f"reviewer:{event.payload.task_id}")

    async def _run(self, event: BusEnvelope[AssignAgentPayload]) -> None:
        ctx = event.payload.reviewer_context
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_STARTED,
                source="reviewer",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.payload.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=TaskStartedReportPayload(
                    task_id=event.payload.task_id,
                    agent_role="reviewer",
                    assignment_id=event.payload.assignment_id,
                    summary="reviewer 已开始审核",
                ),
            )
        )
        if ctx is None or not (ctx.candidate_result_text or ctx.candidate_summary):
            findings = [
                ReviewItem(
                    item_id=f"finding_{event.payload.task_id}",
                    severity="high",
                    label="missing_result",
                    reason="worker 没有提供可审核的结果",
                    required_action="请重新产出结果",
                )
            ]
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.TASK_REPORT_REJECTED,
                    source="reviewer",
                    target="runtime",
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    task_id=event.payload.task_id,
                    correlation_id=event.correlation_id,
                    causation_id=event.event_id,
                    payload=TaskRejectedReportPayload(
                        task_id=event.payload.task_id,
                        review_id=ctx.review_id if ctx is not None else f"review_{event.payload.task_id}",
                        summary="审核未通过",
                        rejection_reason="missing_result",
                        findings=findings,
                    ),
                )
            )
            return
        await self._bus.publish(
            build_envelope(
                event_type=EventType.TASK_REPORT_APPROVED,
                source="reviewer",
                target="runtime",
                session_id=event.session_id,
                turn_id=event.turn_id,
                task_id=event.payload.task_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                payload=TaskApprovedReportPayload(
                    task_id=event.payload.task_id,
                    review_id=ctx.review_id or f"review_{event.payload.task_id}",
                    summary="审核通过",
                    notes="结果满足当前审核条件",
                ),
            )
        )
__all__ = ["AgentTeam", "WorkerOutcome"]
