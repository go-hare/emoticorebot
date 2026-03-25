"""Standalone brain kernel entrypoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .memory import CoreMemory, JsonlMemoryStore, MemoryView, make_id
from .models import (
    BrainEvent,
    BrainEventType,
    BrainOutput,
    BrainResponse,
    BrainTurnContext,
    FrontEvent,
    TaskType,
    ToolResult,
    TurnRouteKind,
)
from .resident import BrainKernelResidentMixin, PendingSleepJob
from .routing import BrainKernelRoutingMixin
from .run_store import Run, RunStore
from .sleep_agent import SleepAgent
from .tooling import BaseToolRule, ToolRulesSolver
from .turns import BrainKernelTurnMixin, PendingRunState


class TaskRouteDecision(BaseModel):
    task_type: TaskType = TaskType.simple


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
        task_router_model: Any | None = None,
        system_prompt: str = "",
        max_steps: int = 8,
    ) -> None:
        self.agent_id = agent_id.strip() or "agent"
        self.model = model
        self.task_router_model = task_router_model
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
        self._front_reply_events: dict[tuple[str, str], asyncio.Event] = {}
        self._front_reply_cache: dict[tuple[str, str], str] = {}

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
        memory_view = memory or self._build_memory_view(conversation_id=conversation_id, query=text)
        task_type = await self._classify_task_type(
            conversation_id=conversation_id,
            text=text,
            memory=memory_view,
            latest_front_reply=latest_front_reply,
        )
        if task_type == TaskType.none:
            return BrainResponse(
                task_type=TaskType.none,
                context=self.build_turn_context(
                    conversation_id=conversation_id,
                    input_kind="user",
                    input_text=text,
                    memory=memory_view,
                    tool_solver=self._make_tool_solver(),
                    available_tools=[],
                ),
                conversation=self.get_conversation_state(conversation_id),
            )

        resolved_metadata = dict(metadata or {})
        resolved_metadata.setdefault("task_type", task_type.value)
        route = self.route_turn(
            conversation_id=conversation_id,
            text=text,
            target_run_id=target_run_id,
            metadata=resolved_metadata,
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
            memory=memory_view,
            model=model,
            system_prompt=system_prompt,
            latest_front_reply=latest_front_reply,
            max_steps=max_steps,
            background=background,
            task_type=task_type,
            existing_run=existing_run,
            route=route,
            metadata=resolved_metadata,
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
            task_type=TaskType.simple,
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
        self._remember_front_reply(
            conversation_id=conversation_id,
            turn_id=resolved_turn_id,
            front_reply=event.front_reply,
        )
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
        self._front_reply_events = {}
        self._front_reply_cache = {}
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
        event_id: str = "",
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
            event_id=event_id or make_id("brain_event"),
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
        event_id: str = "",
        conversation_id: str,
        text: str,
        user_id: str = "",
        turn_id: str = "",
        latest_front_reply: str = "",
        background: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event = BrainEvent(
            event_id=event_id or make_id("brain_event"),
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
        event_id: str = "",
        run_id: str,
        tool_results: Sequence[ToolResult | dict[str, Any]] | ToolResult | dict[str, Any],
        latest_front_reply: str = "",
    ) -> str:
        event = BrainEvent(
            event_id=event_id or make_id("brain_event"),
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
        event_id: str = "",
        conversation_id: str,
        front_event: FrontEvent | dict[str, Any],
        user_id: str = "",
        turn_id: str = "",
    ) -> str:
        event = BrainEvent(
            event_id=event_id or make_id("brain_event"),
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

    async def _classify_task_type(
        self,
        *,
        conversation_id: str,
        text: str,
        memory: MemoryView,
        latest_front_reply: str = "",
    ) -> TaskType:
        llm = self.task_router_model or self.model
        if llm is None:
            return TaskType.simple

        context = self.build_turn_context(
            conversation_id=conversation_id,
            input_kind="user",
            input_text=text,
            memory=memory,
            tool_solver=self._make_tool_solver(),
            available_tools=[],
        )
        messages = [
            SystemMessage(
                content=(
                    "You are a task-type router for a companion robot kernel.\n"
                    'Return STRICT JSON only with exactly one field: {"task_type": "none" | "simple" | "complex"}.\n'
                    "No markdown. No prose. No extra keys.\n"
                    "Classify only the CURRENT user turn; memory and active runs are secondary context.\n"
                    "Decision rules (priority order):\n"
                    '1) If the turn is run-control (cancel/switch/resume/continue/stop current run), output "simple".\n'
                    '2) If Latest Front Reply already directly answers the current user input and no extra action/tool/run is required, output "none".\n'
                    '3) Output "none" only when this is purely social/emotional chat with no concrete deliverable now.\n'
                    "   Typical none examples: greeting/thanks/apology, emotional sharing, relationship talk, "
                    "name preference, pure chit-chat like \"在吗\" \"你今天怎么样\".\n"
                    '4) Output "simple" when there is one bounded request that can be completed in one focused run.\n'
                    "   Typical simple examples: ask current time/date, quick factual Q&A, run one command, "
                    "search/check one issue, summarize one source, fix one concrete bug.\n"
                    '5) Output "complex" only for multi-phase or broad project work with multiple deliverables/dependencies.\n'
                    "   Typical complex examples: architecture redesign, migration plan + implementation roadmap, "
                    "multi-module refactor requiring staged execution.\n"
                    "Anti-confusion rules:\n"
                    '- Do NOT choose "none" if the user asks for any concrete answer/action now (including quick asks like current time).\n'
                    '- Exception: if Latest Front Reply already gave that concrete answer and user did not ask for further action, choose "none".\n'
                    '- Do NOT choose "complex" for single-step asks just because they are technical.\n'
                    '- If uncertain between "none" and "simple", choose "simple" only when a concrete immediate output is requested.\n'
                    '- If uncertain between "simple" and "complex", choose "simple".'
                )
            ),
            HumanMessage(
                content="\n\n".join(
                    [
                        self._build_input_prompt(context=context),
                        "## Latest Front Reply",
                        str(latest_front_reply or "").strip() or "(empty)",
                    ]
                )
            ),
        ]

        try:
            response = await self._invoke_task_router(messages=messages, model=llm)
            return self._coerce_task_type(response)
        except Exception as exc:
            print(f"Task type error: {exc}")
            return TaskType.simple

    async def _invoke_task_router(self, *, messages: list[Any], model: Any) -> Any:
        if hasattr(model, "with_structured_output"):
            try:
                try:
                    structured = model.with_structured_output(
                        TaskRouteDecision,
                        method="function_calling",
                    )
                except TypeError:
                    structured = model.with_structured_output(TaskRouteDecision)
                if hasattr(structured, "ainvoke"):
                    return await structured.ainvoke(messages)
                if hasattr(structured, "invoke"):
                    return structured.invoke(messages)
            except Exception:
                pass

        if hasattr(model, "ainvoke"):
            return await model.ainvoke(messages)
        if hasattr(model, "invoke"):
            return model.invoke(messages)
        raise RuntimeError("Task router model does not support invoke or ainvoke.")

    def _coerce_task_type(self, response: Any) -> TaskType:
        if isinstance(response, TaskRouteDecision):
            return response.task_type
        if isinstance(response, BaseModel):
            try:
                return TaskRouteDecision.model_validate(response.model_dump()).task_type
            except Exception:
                return TaskType.simple
        if isinstance(response, dict):
            try:
                return TaskRouteDecision.model_validate(response).task_type
            except Exception:
                return TaskType.simple

        content = getattr(response, "content", response)
        if isinstance(content, str):
            return self._parse_task_type_json(content)
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return self._parse_task_type_json("\n".join(parts))
        return self._parse_task_type_json(str(content))

    def _parse_task_type_json(self, text: str) -> TaskType:
        value = str(text or "").strip()
        if not value:
            return TaskType.simple
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            start = value.find("{")
            end = value.rfind("}")
            if start < 0 or end <= start:
                return TaskType.simple
            payload = json.loads(value[start : end + 1])
        if not isinstance(payload, dict):
            return TaskType.simple
        try:
            return TaskRouteDecision.model_validate(payload).task_type
        except Exception:
            return TaskType.simple

    def _build_system_prompt(self, *, system_prompt: str, context: BrainTurnContext) -> str:
        prompt = system_prompt.strip() or self.system_prompt or (
            "You are the single brain of a companion robot. "
            "Use tools when needed. When enough information is available, answer directly."
        )
        prompt = (
            f"{prompt}\n\n"
            "## INTERNAL OUTPUT POLICY\n"
            "- task_type/route/run-control labels are internal state; do not expose them in normal replies.\n"
            "- Do not output meta text like `task_type: simple/none/complex` unless the user explicitly asks for debugging internals.\n"
            "- For direct user questions, answer naturally and directly."
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

    def _remember_front_reply(self, *, conversation_id: str, turn_id: str, front_reply: str) -> None:
        reply = str(front_reply or "").strip()
        if not conversation_id or not turn_id or not reply:
            return
        key = (conversation_id, turn_id)
        self._front_reply_cache[key] = reply
        waiter = self._front_reply_events.get(key)
        if waiter is not None:
            waiter.set()

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
