"""Turn execution helpers for the brain kernel."""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .memory import CognitiveEvent, MemoryPatch, MemoryView, make_id
from .models import BrainResponse, BrainTurnContext, PendingToolCall, ToolResult, TurnRoute, TurnRouteKind
from .resident import PendingSleepJob
from .run_store import Run
from .sleep_agent import SleepOutcome
from .tooling import ClientTool, FunctionTool, ToolExecutionRecord, ToolRulesSolver


def extract_message_text(msg: Any) -> str:
    """Extract plain text from a LangChain message."""

    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content).strip()


@dataclass
class PendingRunState:
    run: Run
    context: BrainTurnContext
    model: Any
    tool_entries: list[dict[str, Any]]
    messages: list[Any]
    tool_rules: ToolRulesSolver
    turn_id: str
    user_id: str
    latest_input_text: str
    latest_front_reply: str
    max_steps: int
    steps_taken: int = 0
    last_function_response: str | None = None
    route: TurnRoute | None = None
    tool_trace: list[ToolExecutionRecord] = field(default_factory=list)
    pending_tool_calls: list[PendingToolCall] = field(default_factory=list)


class BrainKernelTurnMixin:
    async def handle_tool_results(
        self,
        *,
        run_id: str,
        tool_results: Sequence[ToolResult | dict[str, Any]] | ToolResult | dict[str, Any],
        latest_front_reply: str = "",
    ) -> BrainResponse:
        state = self._pending_runs.get(run_id)
        if state is None:
            raise RuntimeError(f"Run {run_id} is not waiting for client tool results.")

        results = self._coerce_tool_results(tool_results)
        if not results:
            raise ValueError("handle_tool_results requires at least one tool result.")

        pending_by_id = {item.tool_call_id: item for item in state.pending_tool_calls}
        delivered_ids: set[str] = set()

        for tool_result in results:
            pending_call = pending_by_id.get(tool_result.tool_call_id)
            if pending_call is None:
                raise ValueError(f"Unknown tool_call_id for run {run_id}: {tool_result.tool_call_id}")
            if tool_result.tool_name and tool_result.tool_name != pending_call.tool_name:
                raise ValueError(
                    f"Tool name mismatch for {tool_result.tool_call_id}: "
                    f"expected {pending_call.tool_name}, got {tool_result.tool_name}"
                )

            result_text = self._stringify_tool_output(tool_result.result)
            state.tool_rules.register_tool_call(pending_call.tool_name)
            state.run = self.mark_run_running(state.run.id, current_tool=pending_call.tool_name)
            self._record_tool_result(
                state=state,
                tool_call_id=tool_result.tool_call_id,
                tool_name=pending_call.tool_name,
                args=pending_call.args,
                result_text=result_text,
                success=tool_result.success,
            )
            delivered_ids.add(tool_result.tool_call_id)

        state.pending_tool_calls = [item for item in state.pending_tool_calls if item.tool_call_id not in delivered_ids]
        if latest_front_reply:
            state.latest_front_reply = latest_front_reply

        if state.pending_tool_calls:
            state.run = self.mark_run_running(state.run.id, current_tool=state.pending_tool_calls[0].tool_name)
            self._pending_runs[state.run.id] = state
            return self._make_response(
                run=state.run,
                context=state.context,
                tool_trace=list(state.tool_trace),
                pending_tool_calls=list(state.pending_tool_calls),
                route=state.route,
            )

        self._pending_runs.pop(state.run.id, None)
        state.route = TurnRoute(
            kind=TurnRouteKind.continue_run,
            target_run_id=state.run.id,
            reason="tool results resumed run",
        )
        return await self._advance_run(state)

    async def run_sleep_cycle(
        self,
        *,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        latest_user_text: str,
        latest_front_reply: str,
    ) -> SleepOutcome | None:
        if self.sleep_agent is None or not user_id:
            return None
        return await self.sleep_agent.run_for_turn(
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            latest_user_text=latest_user_text,
            latest_front_reply=latest_front_reply,
        )

    async def _start_turn(
        self,
        *,
        conversation_id: str,
        input_kind: str,
        input_text: str,
        user_id: str,
        turn_id: str,
        tools: Sequence[Any] | None,
        memory: MemoryView | None,
        model: Any | None,
        system_prompt: str,
        latest_front_reply: str,
        max_steps: int | None,
        background: bool | None,
        existing_run: Run | None,
        route: TurnRoute | None,
        metadata: dict[str, Any] | None,
    ) -> BrainResponse:
        llm = model or self.model
        if llm is None:
            raise RuntimeError("BrainKernel requires a LangChain model.")

        turn_id = turn_id or make_id("turn")
        selected_tools = self.tools if tools is None else tools
        tool_entries = self._normalize_tools(selected_tools)
        tool_names = [entry["name"] for entry in tool_entries]
        tool_solver = self._make_tool_solver()
        run_metadata = dict(metadata or {})
        if route is not None:
            run_metadata.setdefault("route_kind", route.kind)
            if route.reason:
                run_metadata.setdefault("route_reason", route.reason)

        if existing_run is None:
            effective_background = background
            if route is not None:
                effective_background = route.kind == TurnRouteKind.start_background
            run = self.create_run(
                conversation_id=conversation_id,
                goal=input_text,
                background=effective_background,
                metadata=run_metadata,
            )
        else:
            merged_metadata = {**existing_run.metadata, **run_metadata}
            merged_metadata["last_user_input"] = input_text
            run = self.run_store.update_run(existing_run.id, metadata=merged_metadata)

        if route is not None:
            run = self._apply_turn_route(conversation_id=conversation_id, run=run, route=route)
        memory_view = memory or self._build_memory_view(conversation_id=conversation_id, query=input_text)
        context = self.build_turn_context(
            conversation_id=conversation_id,
            input_kind=input_kind,
            input_text=input_text,
            memory=memory_view,
            tool_solver=tool_solver,
            available_tools=tool_names,
        )

        if self.memory_store is not None:
            self.memory_store.append_brain_record(
                conversation_id,
                {
                    "agent_id": self.agent_id,
                    "turn_id": turn_id,
                    "role": input_kind,
                    "content": input_text,
                },
            )

        messages: list[Any] = [
            SystemMessage(content=self._build_system_prompt(system_prompt=system_prompt, context=context)),
            HumanMessage(content=self._build_input_prompt(context=context)),
        ]

        state = PendingRunState(
            run=run,
            context=context,
            model=llm,
            tool_entries=tool_entries,
            messages=messages,
            tool_rules=tool_solver,
            turn_id=turn_id,
            user_id=user_id,
            latest_input_text=input_text,
            latest_front_reply=latest_front_reply,
            max_steps=max_steps or self.max_steps,
            route=route,
        )
        return await self._advance_run(state)

    async def _advance_run(self, state: PendingRunState) -> BrainResponse:
        tool_names = [entry["name"] for entry in state.tool_entries]

        while state.steps_taken < state.max_steps:
            allowed_names = state.tool_rules.get_allowed_tool_names(
                set(tool_names),
                error_on_empty=False,
                last_function_response=state.last_function_response,
            )
            active_tools = [entry for entry in state.tool_entries if entry["name"] in allowed_names]
            bound_model = self._bind_tools(state.model, active_tools)
            response = await self._ainvoke(bound_model, state.messages)
            state.messages.append(response)
            state.steps_taken += 1

            tool_calls = list(getattr(response, "tool_calls", []) or [])
            if not tool_calls:
                reply = extract_message_text(response)
                return await self._complete_run(state, reply)

            pending_tool_calls: list[PendingToolCall] = []
            for index, tool_call in enumerate(tool_calls):
                name = str(tool_call.get("name", "") or "").strip()
                if not name:
                    continue

                raw_args = self._normalize_tool_args(tool_call.get("args"))
                args = {**raw_args, **state.tool_rules.get_prefilled_args(name)}
                tool_call_id = str(tool_call.get("id", "") or make_id("toolcall"))
                entry = next((item for item in state.tool_entries if item["name"] == name), None)

                if entry is None:
                    self._record_tool_result(
                        state=state,
                        tool_call_id=tool_call_id,
                        tool_name=name,
                        args=args,
                        result_text=f"Error: Tool '{name}' is not registered.",
                        success=False,
                    )
                    continue

                if entry["mode"] == "client":
                    pending_tool_calls = self._collect_pending_client_tools(tool_calls[index:], state=state)
                    break

                state.tool_rules.register_tool_call(name)
                state.run = self.mark_run_running(state.run.id, current_tool=name)
                result_text, success = await self._execute_tool_entry(entry, args)
                self._record_tool_result(
                    state=state,
                    tool_call_id=tool_call_id,
                    tool_name=name,
                    args=args,
                    result_text=result_text,
                    success=success,
                )

            if pending_tool_calls:
                state.pending_tool_calls = pending_tool_calls
                state.run = self.mark_run_running(state.run.id, current_tool=pending_tool_calls[0].tool_name)
                self._pending_runs[state.run.id] = state
                return self._make_response(
                    run=state.run,
                    context=state.context,
                    tool_trace=list(state.tool_trace),
                    pending_tool_calls=list(pending_tool_calls),
                    route=state.route,
                )

        run = self.fail_run(state.run.id, error="Brain loop exceeded max_steps")
        raise RuntimeError(f"Brain loop exceeded max_steps={state.max_steps} for run {run.id}")

    async def _complete_run(self, state: PendingRunState, reply: str) -> BrainResponse:
        state.run = self.finish_run(state.run.id, result_summary=reply[:200])
        self._append_cognitive_turn_summary(state=state, reply=reply)
        if self.memory_store is not None:
            self.memory_store.append_brain_record(
                state.context.conversation_id,
                {
                    "agent_id": self.agent_id,
                    "turn_id": state.turn_id,
                    "role": "assistant",
                    "content": reply,
                },
            )

        sleep_outcome: SleepOutcome | None = None
        if self._should_run_sleep_in_background():
            await self._enqueue_sleep_job(
                conversation_id=state.context.conversation_id,
                user_id=state.user_id,
                turn_id=state.turn_id,
                latest_user_text=state.latest_input_text,
                latest_front_reply=state.latest_front_reply or reply,
            )
        else:
            sleep_outcome = await self.run_sleep_cycle(
                conversation_id=state.context.conversation_id,
                user_id=state.user_id,
                turn_id=state.turn_id,
                latest_user_text=state.latest_input_text,
                latest_front_reply=state.latest_front_reply or reply,
            )
        self._pending_runs.pop(state.run.id, None)
        return self._make_response(
            reply=reply,
            run=state.run,
            context=state.context,
            tool_trace=list(state.tool_trace),
            sleep_outcome=sleep_outcome,
            route=state.route,
        )

    def _append_cognitive_turn_summary(self, *, state: PendingRunState, reply: str) -> None:
        if self.memory_store is None:
            return

        outcome = "tool_loop" if state.tool_trace else "direct_reply"
        summary_source = state.latest_input_text if state.latest_input_text else reply
        summary = self._clip_text(summary_source, limit=120)
        reason = self._clip_text(reply or "turn completed", limit=200)
        cognitive_event = CognitiveEvent(
            event_id=make_id("cog"),
            user_id=state.user_id,
            agent_id=self.agent_id,
            conversation_id=state.context.conversation_id,
            turn_id=state.turn_id,
            summary=summary,
            outcome=outcome,
            reason=reason,
            user_text=state.latest_input_text,
            assistant_text=reply,
            source_event_ids=[state.turn_id],
            metadata={
                "tool_count": len(state.tool_trace),
                "run_id": state.run.id,
            },
        )
        self.memory_store.append_patch(MemoryPatch(cognitive_append=[cognitive_event]))

    def _make_tool_solver(self) -> ToolRulesSolver:
        return ToolRulesSolver(tool_rules=[rule.model_copy(deep=True) for rule in self.tool_rules])

    def _make_response(
        self,
        *,
        run: Run,
        context: BrainTurnContext,
        reply: str = "",
        tool_trace: list[ToolExecutionRecord] | None = None,
        pending_tool_calls: list[PendingToolCall] | None = None,
        sleep_outcome: SleepOutcome | None = None,
        route: TurnRoute | None = None,
    ) -> BrainResponse:
        return BrainResponse(
            reply=reply,
            run=run,
            context=context,
            tool_trace=list(tool_trace or []),
            pending_tool_calls=list(pending_tool_calls or []),
            sleep_outcome=sleep_outcome,
            route=route,
            conversation=self.get_conversation_state(context.conversation_id),
        )

    def _should_run_sleep_in_background(self) -> bool:
        return self.sleep_agent is not None and self._sleep_queue is not None and self.is_running

    async def _enqueue_sleep_job(
        self,
        *,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        latest_user_text: str,
        latest_front_reply: str,
    ) -> None:
        if self._sleep_queue is None or self.sleep_agent is None or not user_id:
            return
        await self._sleep_queue.put(
            PendingSleepJob(
                conversation_id=conversation_id,
                user_id=user_id,
                turn_id=turn_id,
                latest_user_text=latest_user_text,
                latest_front_reply=latest_front_reply,
            )
        )

    def _normalize_tools(self, tools: Sequence[Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, FunctionTool):
                entries.append({"name": tool.name, "binding": tool.to_schema(), "executor": tool, "mode": "local"})
                continue
            if isinstance(tool, ClientTool):
                entries.append({"name": tool.name, "binding": tool.to_schema(), "executor": None, "mode": "client"})
                continue

            name = str(getattr(tool, "name", "") or "").strip()
            if not name:
                raise ValueError(f"Unsupported tool without name: {tool!r}")

            if hasattr(tool, "to_schema"):
                binding = tool.to_schema()
            else:
                binding = tool

            has_executor = any(hasattr(tool, attr) for attr in ("execute", "ainvoke", "invoke")) or callable(tool)
            entries.append(
                {
                    "name": name,
                    "binding": binding,
                    "executor": tool if has_executor else None,
                    "mode": "local" if has_executor else "client",
                }
            )
        return entries

    def _bind_tools(self, model: Any, tool_entries: list[dict[str, Any]]) -> Any:
        if not tool_entries:
            return model
        if not hasattr(model, "bind_tools"):
            raise RuntimeError("Model does not support bind_tools, but tools were provided.")
        bindings = [entry["binding"] for entry in tool_entries]
        return model.bind_tools(bindings)

    async def _ainvoke(self, model: Any, messages: list[Any]) -> AIMessage:
        if hasattr(model, "ainvoke"):
            response = await model.ainvoke(messages)
        elif hasattr(model, "invoke"):
            response = model.invoke(messages)
        else:
            raise RuntimeError("LangChain model does not support invoke or ainvoke.")
        if isinstance(response, AIMessage):
            return response
        return AIMessage(content=extract_message_text(response))

    def _normalize_tool_args(self, args: Any) -> dict[str, Any]:
        if isinstance(args, dict):
            return dict(args)
        if isinstance(args, str) and args.strip():
            try:
                payload = json.loads(args)
            except json.JSONDecodeError:
                return {}
            if isinstance(payload, dict):
                return payload
        return {}

    async def _execute_tool_entry(self, entry: dict[str, Any], args: dict[str, Any]) -> tuple[str, bool]:
        executor = entry["executor"]
        try:
            if hasattr(executor, "execute"):
                result = executor.execute(**args)
            elif hasattr(executor, "ainvoke"):
                result = executor.ainvoke(args)
            elif hasattr(executor, "invoke"):
                result = executor.invoke(args)
            elif callable(executor):
                result = executor(**args)
            else:
                raise TypeError(f"Unsupported tool executor: {executor!r}")

            if inspect.isawaitable(result):
                result = await result
            result_text = self._stringify_tool_output(result)
            return result_text, not result_text.startswith("Error")
        except Exception as exc:
            return f"Error executing {entry['name']}: {exc}", False

    def _record_tool_result(
        self,
        *,
        state: PendingRunState,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        result_text: str,
        success: bool,
    ) -> None:
        if self.memory_store is not None:
            self.memory_store.append_tool_record(
                state.context.conversation_id,
                {
                    "agent_id": self.agent_id,
                    "turn_id": state.turn_id,
                    "tool_name": tool_name,
                    "content": result_text,
                },
            )

        state.tool_trace.append(
            ToolExecutionRecord(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args,
                result=result_text,
                success=success,
            )
        )
        state.messages.append(ToolMessage(content=result_text, tool_call_id=tool_call_id))
        state.last_function_response = json.dumps({"message": result_text}, ensure_ascii=False)

    def _collect_pending_client_tools(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        state: PendingRunState,
    ) -> list[PendingToolCall]:
        pending: list[PendingToolCall] = []
        for tool_call in tool_calls:
            name = str(tool_call.get("name", "") or "").strip()
            if not name:
                continue
            entry = next((item for item in state.tool_entries if item["name"] == name), None)
            if entry is None or entry["mode"] != "client":
                continue
            args = {**self._normalize_tool_args(tool_call.get("args")), **state.tool_rules.get_prefilled_args(name)}
            pending.append(
                PendingToolCall(
                    tool_call_id=str(tool_call.get("id", "") or make_id("toolcall")),
                    tool_name=name,
                    args=args,
                )
            )
        return pending

    def _coerce_tool_results(
        self,
        tool_results: Sequence[ToolResult | dict[str, Any]] | ToolResult | dict[str, Any],
    ) -> list[ToolResult]:
        if isinstance(tool_results, ToolResult):
            return [tool_results]
        if isinstance(tool_results, dict):
            return [ToolResult.model_validate(tool_results)]
        return [item if isinstance(item, ToolResult) else ToolResult.model_validate(item) for item in tool_results]

    def _stringify_tool_output(self, result: Any) -> str:
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return str(result)
