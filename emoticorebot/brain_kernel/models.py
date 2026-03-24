"""Public enums and payload models for the brain kernel."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .memory import make_id
from .run_store import Run
from .sleep_agent import SleepOutcome
from .tooling import ToolExecutionRecord


class BrainTurnContext(BaseModel):
    agent_id: str
    conversation_id: str
    input_kind: str
    input_text: str
    core_memory: str
    foreground_run_id: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    active_runs: list[Run] = Field(default_factory=list)
    tool_rule_prompt: str = ""


class PendingToolCall(BaseModel):
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_call_id: str
    result: Any = ""
    tool_name: str = ""
    success: bool = True


class FrontEvent(BaseModel):
    event_type: str = "dialogue"
    user_text: str = ""
    front_reply: str = ""
    emotion: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrainEventType(StrEnum):
    user_input = "user_input"
    observation = "observation"
    front_event = "front_event"
    tool_results = "tool_results"
    shutdown = "shutdown"


class BrainEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: make_id("brain_event"))
    type: BrainEventType
    conversation_id: str = ""
    run_id: str = ""
    target_run_id: str = ""
    text: str = ""
    user_id: str = ""
    turn_id: str = ""
    latest_front_reply: str = ""
    tool_results: list[ToolResult] = Field(default_factory=list)
    front_event: FrontEvent | None = None
    background: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnRouteKind(StrEnum):
    start_foreground = "start_foreground"
    start_background = "start_background"
    continue_run = "continue_run"
    switch_run = "switch_run"
    cancel_run = "cancel_run"


class TaskType(StrEnum):
    none = "none"
    simple = "simple"
    complex = "complex"


class TurnRoute(BaseModel):
    kind: TurnRouteKind
    target_run_id: str = ""
    reason: str = ""


class ConversationState(BaseModel):
    conversation_id: str
    foreground_run_id: str = ""
    active_run_ids: list[str] = Field(default_factory=list)
    background_run_ids: list[str] = Field(default_factory=list)


class BrainResponse(BaseModel):
    task_type: TaskType = TaskType.simple
    reply: str = ""
    run: Run | None = None
    context: BrainTurnContext | None = None
    tool_trace: list[ToolExecutionRecord] = Field(default_factory=list)
    pending_tool_calls: list[PendingToolCall] = Field(default_factory=list)
    sleep_outcome: SleepOutcome | None = None
    route: TurnRoute | None = None
    conversation: ConversationState | None = None


class BrainOutputType(StrEnum):
    response = "response"
    recorded = "recorded"
    error = "error"
    stopped = "stopped"


class BrainOutput(BaseModel):
    event_id: str
    type: BrainOutputType
    response: BrainResponse | None = None
    error: str = ""
