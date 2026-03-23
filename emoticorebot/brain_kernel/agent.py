"""Standalone brain kernel entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .memory import CoreMemory, JsonlMemoryStore, MemoryView, make_id
from .models import (
    BrainEvent,
    BrainEventType,
    BrainOutput,
    BrainResponse,
    BrainTurnContext,
    FrontEvent,
    ToolResult,
    TurnRouteKind,
)
from .resident import BrainKernelResidentMixin, PendingSleepJob
from .routing import BrainKernelRoutingMixin
from .run_store import Run, RunStore
from .sleep_agent import SleepAgent
from .tooling import BaseToolRule, ToolRulesSolver
from .turns import BrainKernelTurnMixin, PendingRunState


class BrainKernel(BrainKernelTurnMixin, BrainKernelRoutingMixin, BrainKernelResidentMixin):
    """Single-agent brain service with pause/resume tool loop."""

    def __init__(
        self,
        *,
        agent_id: str = "agent",
        model: Any | None = None,
        tools: Sequence[Any] | None = None,
        tool_rules: list[BaseToolRule] | None = None,
        run_store: RunStore | None = None,
        memory_store: JsonlMemoryStore | None = None,
        sleep_agent: SleepAgent | None = None,
        system_prompt: str = "",
        max_steps: int = 8,
    ) -> None:
        self.agent_id = agent_id.strip() or "agent"
        self.model = model
        self.tools = list(tools or [])
        self.run_store = run_store or RunStore()
        self.memory_store = memory_store
        self.sleep_agent = sleep_agent
        if self.sleep_agent is not None and getattr(self.sleep_agent, "model", None) is None:
            self.sleep_agent.model = model
        self.system_prompt = system_prompt.strip()
        self.max_steps = max_steps
        self.tool_rules = [rule.model_copy(deep=True) for rule in (tool_rules or [])]
        self._pending_runs: dict[str, PendingRunState] = {}
        self._event_queue: asyncio.Queue[BrainEvent] | None = None
        self._output_queue: asyncio.Queue[BrainOutput] | None = None
        self._resident_task: asyncio.Task[None] | None = None
        self._conversation_foregrounds: dict[str, str] = {}
        self._conversation_queues: dict[str, asyncio.Queue[BrainEvent]] = {}
        self._conversation_tasks: dict[str, asyncio.Task[None]] = {}
        self._sleep_queue: asyncio.Queue[PendingSleepJob | None] | None = None
        self._sleep_worker_task: asyncio.Task[None] | None = None

    def build_turn_context(
        self,
        *,
        conversation_id: str,
        input_kind: str,
        input_text: str,
        memory: MemoryView,
        tool_solver: ToolRulesSolver,
        available_tools: list[str] | tuple[str, ...] = (),
        last_function_response: str | None = None,
    ) -> BrainTurnContext:
        tool_names = {str(name).strip() for name in available_tools if str(name).strip()}
        rule_prompt = tool_solver.compile_rule_prompt()
        conversation = self.get_conversation_state(conversation_id)
        return BrainTurnContext(
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            input_kind=input_kind,
            input_text=input_text,
            core_memory=CoreMemory.from_memory_view(memory).render(tool_usage_rules=rule_prompt or None),
            foreground_run_id=conversation.foreground_run_id,
            allowed_tools=tool_solver.get_allowed_tool_names(
                tool_names,
                error_on_empty=False,
                last_function_response=last_function_response,
            ),
            active_runs=self.run_store.list_active_runs(agent_id=self.agent_id, conversation_id=conversation_id),
            tool_rule_prompt=rule_prompt,
        )

    async def handle_user_input(
        self,
        *,
        conversation_id: str,
        text: str,
        user_id: str = "",
        turn_id: str = "",
        tools: Sequence[Any] | None = None,
        memory: MemoryView | None = None,
        model: Any | None = None,
        system_prompt: str = "",
        latest_front_reply: str = "",
        max_steps: int | None = None,
        background: bool | None = None,
        target_run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> BrainResponse:
        route = self.route_turn(
            conversation_id=conversation_id,
            text=text,
            target_run_id=target_run_id,
            metadata=metadata,
        )
        if route.kind in {TurnRouteKind.switch_run, TurnRouteKind.cancel_run}:
            return self._handle_control_turn(
                conversation_id=conversation_id,
                text=text,
                route=route,
            )
        existing_run = self.get_run(route.target_run_id) if route.target_run_id else None
        return await self._start_turn(
            conversation_id=conversation_id,
            input_kind="user",
            input_text=text,
            user_id=user_id,
            turn_id=turn_id,
            tools=tools,
            memory=memory,
            model=model,
            system_prompt=system_prompt,
            latest_front_reply=latest_front_reply,
            max_steps=max_steps,
            background=background,
            existing_run=existing_run,
            route=route,
            metadata=metadata,
        )

    async def handle_observation(
        self,
        *,
        conversation_id: str,
        text: str,
        user_id: str = "",
        turn_id: str = "",
        tools: Sequence[Any] | None = None,
        memory: MemoryView | None = None,
        model: Any | None = None,
        system_prompt: str = "",
        latest_front_reply: str = "",
        max_steps: int | None = None,
        background: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrainResponse:
        return await self._start_turn(
            conversation_id=conversation_id,
            input_kind="observation",
            input_text=text,
            user_id=user_id,
            turn_id=turn_id,
            tools=tools,
            memory=memory,
            model=model,
            system_prompt=system_prompt,
            latest_front_reply=latest_front_reply,
            max_steps=max_steps,
            background=background,
            existing_run=None,
            route=None,
            metadata=metadata,
        )

    async def handle_front_event(
        self,
        *,
        conversation_id: str,
        front_event: FrontEvent | dict[str, Any],
        user_id: str = "",
        turn_id: str = "",
    ) -> FrontEvent:
        event = self._coerce_front_event(front_event)
        if self.memory_store is None:
            return event

        resolved_turn_id = turn_id or make_id("front_turn")
        self.memory_store.append_front_record(
            conversation_id,
            {
                "agent_id": self.agent_id,
                "user_id": user_id,
                "turn_id": resolved_turn_id,
                "event_type": event.event_type,
                "user_text": event.user_text,
                "front_reply": event.front_reply,
                "emotion": event.emotion,
                "tags": list(event.tags),
                "metadata": dict(event.metadata),
                "content": self._summarize_front_event(event),
            },
        )
        return event

    async def start(self) -> None:
        if self._resident_task is not None and not self._resident_task.done():
            return
        if self.model is None:
            raise RuntimeError("BrainKernel.start requires a default model on the kernel.")
        self._event_queue = asyncio.Queue()
        self._output_queue = asyncio.Queue()
        self._conversation_foregrounds = {}
        self._conversation_queues = {}
        self._conversation_tasks = {}
        self._sleep_queue = asyncio.Queue() if self.sleep_agent is not None else None
        self._sleep_worker_task = (
            asyncio.create_task(self._sleep_worker_loop()) if self._sleep_queue is not None else None
        )
        self._resident_task = asyncio.create_task(self._resident_loop())

    async def stop(self) -> None:
        if self._resident_task is None:
            return
        if self._event_queue is not None and not self._resident_task.done():
            await self._event_queue.put(BrainEvent(type=BrainEventType.shutdown))
        await self._resident_task
        self._resident_task = None

    @property
    def is_running(self) -> bool:
        return self._resident_task is not None and not self._resident_task.done()

    async def publish_user_input(
        self,
        *,
        conversation_id: str,
        text: str,
        user_id: str = "",
        turn_id: str = "",
        latest_front_reply: str = "",
        background: bool | None = None,
        target_run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event = BrainEvent(
            type=BrainEventType.user_input,
            conversation_id=conversation_id,
            target_run_id=target_run_id,
            text=text,
            user_id=user_id,
            turn_id=turn_id,
            latest_front_reply=latest_front_reply,
            background=background,
            metadata=dict(metadata or {}),
        )
        await self._put_event(event)
        return event.event_id

    async def publish_observation(
        self,
        *,
        conversation_id: str,
        text: str,
        user_id: str = "",
        turn_id: str = "",
        latest_front_reply: str = "",
        background: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event = BrainEvent(
            type=BrainEventType.observation,
            conversation_id=conversation_id,
            text=text,
            user_id=user_id,
            turn_id=turn_id,
            latest_front_reply=latest_front_reply,
            background=background,
            metadata=dict(metadata or {}),
        )
        await self._put_event(event)
        return event.event_id

    async def publish_tool_results(
        self,
        *,
        run_id: str,
        tool_results: Sequence[ToolResult | dict[str, Any]] | ToolResult | dict[str, Any],
        latest_front_reply: str = "",
    ) -> str:
        event = BrainEvent(
            type=BrainEventType.tool_results,
            conversation_id=self._resolve_run_conversation_id(run_id),
            run_id=run_id,
            tool_results=self._coerce_tool_results(tool_results),
            latest_front_reply=latest_front_reply,
        )
        await self._put_event(event)
        return event.event_id

    async def publish_front_event(
        self,
        *,
        conversation_id: str,
        front_event: FrontEvent | dict[str, Any],
        user_id: str = "",
        turn_id: str = "",
    ) -> str:
        event = BrainEvent(
            type=BrainEventType.front_event,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            front_event=self._coerce_front_event(front_event),
        )
        await self._put_event(event)
        return event.event_id

    async def recv_output(self) -> BrainOutput:
        if self._output_queue is None:
            raise RuntimeError("BrainKernel is not running. Call start() first.")
        return await self._output_queue.get()

    def create_run(
        self,
        *,
        conversation_id: str,
        goal: str,
        background: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Run:
        return self.run_store.create_run(
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            goal=goal,
            background=background,
            metadata=metadata,
        )

    def get_run(self, run_id: str) -> Run | None:
        return self.run_store.get_run(run_id)

    def list_runs(self, conversation_id: str | None = None) -> list[Run]:
        return self.run_store.list_runs(agent_id=self.agent_id, conversation_id=conversation_id)

    def _build_memory_view(self, *, conversation_id: str, query: str) -> MemoryView:
        if self.memory_store is None:
            return MemoryView()
        return self.memory_store.build_memory_view(conversation_id, self.agent_id, query)

    def _build_system_prompt(self, *, system_prompt: str, context: BrainTurnContext) -> str:
        prompt = system_prompt.strip() or self.system_prompt or (
            "You are the single brain of a companion robot. "
            "Use tools when needed. When enough information is available, answer directly."
        )
        if context.tool_rule_prompt:
            prompt = f"{prompt}\n\n{context.tool_rule_prompt}"
        return prompt

    def _build_input_prompt(self, *, context: BrainTurnContext) -> str:
        parts = []
        if context.core_memory:
            parts.extend(["## Memory", context.core_memory, ""])
        if context.active_runs:
            parts.append("## Active Runs")
            for run in context.active_runs:
                marker = "foreground" if run.id == context.foreground_run_id else "background"
                parts.append(f"- [{marker}] {run.id}: {run.goal}")
            parts.append("")
        heading = "User Input" if context.input_kind == "user" else "Observation"
        parts.extend([f"## {heading}", context.input_text])
        return "\n".join(parts).strip()

    def _coerce_front_event(self, front_event: FrontEvent | dict[str, Any]) -> FrontEvent:
        if isinstance(front_event, FrontEvent):
            return front_event
        return FrontEvent.model_validate(front_event)

    def _summarize_front_event(self, event: FrontEvent) -> str:
        parts: list[str] = []
        if event.user_text:
            parts.append(f"user={self._clip_text(event.user_text, 120)}")
        if event.front_reply:
            parts.append(f"front={self._clip_text(event.front_reply, 120)}")
        if event.emotion:
            parts.append(f"emotion={event.emotion}")
        if event.tags:
            parts.append(f"tags={','.join(event.tags[:6])}")
        return " | ".join(parts)

    def _clip_text(self, text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."
