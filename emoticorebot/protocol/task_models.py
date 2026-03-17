"""Protocol source-of-truth models shared across the v3 runtime."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentRole = Literal["planner", "worker", "reviewer"]
ReplyKind = Literal["answer", "ask_user", "safety_fallback", "status"]
ReviewPolicy = Literal["skip", "optional", "required"]
PlanStepStatus = Literal["pending", "running", "done", "failed", "skipped"]
ReviewSeverity = Literal["low", "medium", "high", "critical"]
ProvidedInputSource = Literal["user_message", "upload", "sensor", "system"]
TaskVisibleState = Literal["running", "waiting", "done"]
TaskVisibleResult = Literal["none", "success", "failed", "cancelled"]
TaskLifecycleState = Literal[
    "created",
    "assigned",
    "running",
    "planned",
    "waiting_input",
    "reviewing",
    "done",
    "failed",
    "cancelled",
    "archived",
]
TaskControlState = Literal["running", "waiting_input", "completed", "failed"]
TaskResultStatus = Literal["success", "partial", "pending", "failed"]
TraceItem = dict[str, Any]


class ProtocolModel(BaseModel):
    """Base model for all protocol payloads."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ContentBlock(ProtocolModel):
    type: str
    text: str | None = None
    url: str | None = None
    path: str | None = None
    mime_type: str | None = None
    name: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_text_block(self) -> "ContentBlock":
        if self.type == "text" and not self.text:
            raise ValueError("text content blocks require `text`")
        return self


class MessageRef(ProtocolModel):
    channel: str | None = None
    chat_id: str | None = None
    sender_id: str | None = None
    message_id: str | None = None
    reply_to_message_id: str | None = None
    timestamp: str | None = None


class InputRequest(ProtocolModel):
    field: str | None = None
    question: str | None = None
    required: bool | None = None
    expected_type: str | None = None
    choices: list[str] = Field(default_factory=list)
    validation_hint: str | None = None


class PlanStep(ProtocolModel):
    step_id: str | None = None
    title: str | None = None
    description: str | None = None
    role: str | None = None
    status: PlanStepStatus | None = None
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    tools: list[str] = Field(default_factory=list)


class ReviewItem(ProtocolModel):
    item_id: str | None = None
    severity: ReviewSeverity | None = None
    label: str | None = None
    reason: str | None = None
    required_action: str | None = None
    evidence: list[str] = Field(default_factory=list)


class ReplyDraft(ProtocolModel):
    reply_id: str
    kind: ReplyKind
    plain_text: str | None = None
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    safe_fallback: bool = False
    language: str | None = None
    style_hint: str | None = None
    reply_to_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_content(self) -> "ReplyDraft":
        if not self.plain_text and not self.content_blocks:
            raise ValueError("reply drafts require plain_text or content_blocks")
        return self


class TaskStateSnapshot(ProtocolModel):
    task_id: str
    status: str
    state_version: int | None = None
    title: str | None = None
    summary: str | None = None
    error: str | None = None
    assignee: str | None = None
    plan_id: str | None = None
    review_required: bool | None = None
    last_progress: str | None = None
    input_request: InputRequest | None = None
    updated_at: str | None = None


class TaskRequestSpec(ProtocolModel):
    request: str
    title: str | None = None
    goal: str | None = None
    expected_output: str | None = None
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    history_context: str | None = None
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    skill_hints: list[str] = Field(default_factory=list)
    review_policy: ReviewPolicy | None = None
    preferred_agent: Literal["planner", "worker"] | None = None


class ProvidedInputItem(ProtocolModel):
    field: str | None = None
    value_text: str | None = None
    value_json: str | None = None
    attachments: list[ContentBlock] = Field(default_factory=list)
    source: ProvidedInputSource | None = None
    provided_at: str | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "ProvidedInputItem":
        if self.value_text or self.value_json or self.attachments:
            return self
        raise ValueError("provided input items require text, json, or attachments")


class ProvidedInputBundle(ProtocolModel):
    plain_text: str | None = None
    items: list[ProvidedInputItem] = Field(default_factory=list)
    attachments: list[ContentBlock] = Field(default_factory=list)
    source_message: MessageRef | None = None
    source_event_id: str | None = None


class AgentInputContext(ProtocolModel):
    latest_user_message: MessageRef | None = None
    latest_user_text: str | None = None
    latest_attachments: list[ContentBlock] = Field(default_factory=list)
    provided_inputs: ProvidedInputBundle | None = None
    missing_fields: list[str] = Field(default_factory=list)
    dialogue_summary: str | None = None


class ReviewerContext(ProtocolModel):
    review_id: str | None = None
    review_policy: ReviewPolicy | None = None
    candidate_summary: str | None = None
    candidate_result_text: str | None = None
    candidate_result_blocks: list[ContentBlock] = Field(default_factory=list)
    candidate_artifacts: list[ContentBlock] = Field(default_factory=list)
    candidate_confidence: float | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    prior_findings: list[ReviewItem] = Field(default_factory=list)


class ControlParameters(ProtocolModel):
    text: str | None = None
    voice: str | None = None
    tone: str | None = None
    target_pose: str | None = None
    target_location: str | None = None
    speed: float | None = None
    duration_ms: int | None = None
    object_id: str | None = None
    grip_mode: str | None = None
    emergency: bool | None = None


class PerceptionData(ProtocolModel):
    transcript: str | None = None
    speaker_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    bounding_boxes: list[str] = Field(default_factory=list)
    position: str | None = None
    velocity: str | None = None
    map_ref: str | None = None
    raw_ref: str | None = None


# Transitional type aliases kept only so upper layers can be migrated in place.
TaskInputRequest = InputRequest
TaskSpec = TaskRequestSpec
TaskState = dict[str, Any]


__all__ = [
    "AgentInputContext",
    "AgentRole",
    "ContentBlock",
    "ControlParameters",
    "InputRequest",
    "MessageRef",
    "PerceptionData",
    "PlanStep",
    "PlanStepStatus",
    "ProtocolModel",
    "ProvidedInputBundle",
    "ProvidedInputItem",
    "ProvidedInputSource",
    "ReplyDraft",
    "ReplyKind",
    "ReviewItem",
    "ReviewPolicy",
    "ReviewSeverity",
    "ReviewerContext",
    "TaskRequestSpec",
    "TaskVisibleResult",
    "TaskVisibleState",
    "TaskResultStatus",
    "TaskSpec",
    "TaskState",
    "TaskStateSnapshot",
    "TaskControlState",
    "TaskInputRequest",
    "TaskLifecycleState",
    "TraceItem",
]
