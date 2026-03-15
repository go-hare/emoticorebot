"""Assignment command builders for the v3 runtime scheduler."""

from __future__ import annotations

from uuid import uuid4

from emoticorebot.protocol.commands import ArchiveTaskPayload, AssignAgentPayload, CancelAgentPayload, ResumeAgentPayload
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.task_models import (
    AgentInputContext,
    AgentRole,
    MessageRef,
    ProvidedInputBundle,
    ReviewerContext,
)
from emoticorebot.protocol.topics import EventType

from .task_store import RuntimeTaskRecord


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class AssignmentFactory:
    """Generates task, assignment, and review identifiers plus command envelopes."""

    @staticmethod
    def new_task_id() -> str:
        return _new_id("task")

    @staticmethod
    def new_assignment_id() -> str:
        return _new_id("assign")

    @staticmethod
    def new_review_id() -> str:
        return _new_id("review")

    @staticmethod
    def select_initial_role(task: RuntimeTaskRecord) -> AgentRole:
        preferred = task.request.preferred_agent
        if preferred == "planner":
            return "planner"
        return "worker"

    def build_assign_agent(
        self,
        *,
        task: RuntimeTaskRecord,
        agent_role: AgentRole,
        input_context: AgentInputContext | None = None,
        reviewer_context: ReviewerContext | None = None,
        causation_id: str | None = None,
    ) -> BusEnvelope[AssignAgentPayload]:
        return build_envelope(
            event_type=EventType.RUNTIME_ASSIGN_AGENT,
            source="runtime",
            target=agent_role,
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=AssignAgentPayload(
                assignment_id=task.current_assignment_id or self.new_assignment_id(),
                task_id=task.task_id,
                agent_role=agent_role,
                task_state=task.snapshot(),
                task_request=task.request,
                plan_steps=task.plan_steps,
                input_context=input_context,
                reviewer_context=reviewer_context,
            ),
        )

    def build_resume_agent(
        self,
        *,
        task: RuntimeTaskRecord,
        resume_input: ProvidedInputBundle,
        resume_message: MessageRef | None,
        causation_id: str | None = None,
    ) -> BusEnvelope[ResumeAgentPayload]:
        agent_role = task.assignee or "worker"
        return build_envelope(
            event_type=EventType.RUNTIME_RESUME_AGENT,
            source="runtime",
            target=agent_role,
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=ResumeAgentPayload(
                assignment_id=task.current_assignment_id or self.new_assignment_id(),
                task_id=task.task_id,
                agent_role=agent_role,
                task_state=task.snapshot(),
                resume_input=resume_input,
                resume_message=resume_message,
            ),
        )

    def build_cancel_agent(
        self,
        *,
        task: RuntimeTaskRecord,
        reason: str | None,
        causation_id: str | None = None,
    ) -> BusEnvelope[CancelAgentPayload] | None:
        if task.assignee is None:
            return None
        return build_envelope(
            event_type=EventType.RUNTIME_CANCEL_AGENT,
            source="runtime",
            target=task.assignee,
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=CancelAgentPayload(
                task_id=task.task_id,
                agent_role=task.assignee,
                reason=reason,
            ),
        )

    def build_archive_task(
        self,
        *,
        task: RuntimeTaskRecord,
        reason: str | None,
        causation_id: str | None = None,
    ) -> BusEnvelope[ArchiveTaskPayload]:
        return build_envelope(
            event_type=EventType.RUNTIME_ARCHIVE_TASK,
            source="runtime",
            target="runtime",
            session_id=task.session_id,
            turn_id=task.turn_id,
            task_id=task.task_id,
            correlation_id=task.task_id,
            causation_id=causation_id,
            payload=ArchiveTaskPayload(
                task_id=task.task_id,
                archive_reason=reason,
                final_state=task.snapshot(),
            ),
        )


__all__ = ["AssignmentFactory"]
