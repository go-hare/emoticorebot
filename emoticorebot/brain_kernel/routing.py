"""Run routing and control helpers for the brain kernel."""

from __future__ import annotations

from .models import BrainResponse, ConversationState, TurnRoute, TurnRouteKind
from .run_store import Run, RunStatus


class BrainKernelRoutingMixin:
    def get_conversation_state(self, conversation_id: str) -> ConversationState:
        active_runs = self.run_store.list_active_runs(agent_id=self.agent_id, conversation_id=conversation_id)
        active_run_ids = [run.id for run in active_runs]
        foreground_run_id = self._conversation_foregrounds.get(conversation_id, "")
        if foreground_run_id not in active_run_ids:
            foreground_run_id = next((run.id for run in active_runs if not run.background), "")
            if not foreground_run_id and active_run_ids:
                foreground_run_id = active_run_ids[0]
            if foreground_run_id:
                self._conversation_foregrounds[conversation_id] = foreground_run_id
            else:
                self._conversation_foregrounds.pop(conversation_id, None)
        background_run_ids = [run_id for run_id in active_run_ids if run_id != foreground_run_id]
        return ConversationState(
            conversation_id=conversation_id,
            foreground_run_id=foreground_run_id,
            active_run_ids=active_run_ids,
            background_run_ids=background_run_ids,
        )

    def route_turn(
        self,
        *,
        conversation_id: str,
        text: str,
        target_run_id: str = "",
        metadata: dict[str, object] | None = None,
    ) -> TurnRoute:
        payload = dict(metadata or {})
        state = self.get_conversation_state(conversation_id)
        active_runs = {
            run.id: run
            for run in self.run_store.list_active_runs(agent_id=self.agent_id, conversation_id=conversation_id)
        }
        requested_run_id = str(target_run_id or payload.get("target_run_id", "") or "").strip()
        queue_only = bool(payload.get("queue_only"))
        control = str(payload.get("control", "") or "").strip().lower()
        wants_new_task = bool(payload.get("new_task")) or self._looks_like_new_task(text)

        if control == "cancel":
            cancel_run_id = requested_run_id or state.foreground_run_id
            if cancel_run_id and cancel_run_id in active_runs:
                return TurnRoute(
                    kind=TurnRouteKind.cancel_run,
                    target_run_id=cancel_run_id,
                    reason="metadata requested cancel",
                )

        if control == "switch":
            switch_run_id = requested_run_id or self._pick_non_foreground_run_id(state)
            if switch_run_id and switch_run_id in active_runs:
                return TurnRoute(
                    kind=TurnRouteKind.switch_run,
                    target_run_id=switch_run_id,
                    reason="metadata requested switch",
                )

        if requested_run_id and requested_run_id in active_runs and not self._is_run_waiting_for_client_tool(
            requested_run_id
        ):
            return TurnRoute(
                kind=TurnRouteKind.continue_run,
                target_run_id=requested_run_id,
                reason="explicit target_run_id",
            )

        if not active_runs:
            return TurnRoute(kind=TurnRouteKind.start_foreground, reason="no active runs")

        foreground_run = active_runs.get(state.foreground_run_id)
        if wants_new_task:
            route_kind = (
                TurnRouteKind.start_background
                if queue_only and foreground_run is not None
                else TurnRouteKind.start_foreground
            )
            reason = "metadata requested new task" if payload.get("new_task") else "new-task heuristic matched"
            return TurnRoute(kind=route_kind, reason=reason)

        if foreground_run is not None and not self._is_run_waiting_for_client_tool(foreground_run.id):
            return TurnRoute(
                kind=TurnRouteKind.continue_run,
                target_run_id=foreground_run.id,
                reason="continue foreground run",
            )

        return TurnRoute(kind=TurnRouteKind.start_foreground, reason="foreground run is blocked or no longer continuable")

    def mark_run_running(self, run_id: str, current_tool: str = "") -> Run:
        return self.run_store.mark_running(run_id, current_tool=current_tool)

    def finish_run(self, run_id: str, result_summary: str = "") -> Run:
        run = self.run_store.finish_run(run_id, result_summary=result_summary)
        self._refresh_foreground_after_terminal(run.conversation_id, run.id)
        return run

    def fail_run(self, run_id: str, error: str) -> Run:
        self._pending_runs.pop(run_id, None)
        run = self.run_store.fail_run(run_id, error=error)
        self._refresh_foreground_after_terminal(run.conversation_id, run.id)
        return run

    def cancel_run(self, run_id: str, reason: str = "") -> Run:
        self._pending_runs.pop(run_id, None)
        run = self.run_store.cancel_run(run_id, reason=reason)
        self._refresh_foreground_after_terminal(run.conversation_id, run.id)
        return run

    def _apply_turn_route(self, *, conversation_id: str, run: Run, route: TurnRoute) -> Run:
        if route.kind == TurnRouteKind.start_background:
            return self.run_store.update_run(run.id, background=True)
        return self._promote_run_to_foreground(conversation_id=conversation_id, run_id=run.id)

    def _handle_control_turn(
        self,
        *,
        conversation_id: str,
        text: str,
        route: TurnRoute,
    ) -> BrainResponse:
        if route.kind == TurnRouteKind.switch_run:
            run = self._promote_run_to_foreground(conversation_id=conversation_id, run_id=route.target_run_id)
            reply = self._build_control_reply(route=route, run=run, text=text)
            context = self.build_turn_context(
                conversation_id=conversation_id,
                input_kind="user",
                input_text=text,
                memory=self._build_memory_view(conversation_id=conversation_id, query=text),
                tool_solver=self._make_tool_solver(),
                available_tools=[],
            )
            return self._make_response(reply=reply, run=run, context=context, route=route)

        if route.kind == TurnRouteKind.cancel_run:
            run = self.cancel_run(route.target_run_id, reason="cancelled by control turn")
            reply = self._build_control_reply(route=route, run=run, text=text)
            context = self.build_turn_context(
                conversation_id=conversation_id,
                input_kind="user",
                input_text=text,
                memory=self._build_memory_view(conversation_id=conversation_id, query=text),
                tool_solver=self._make_tool_solver(),
                available_tools=[],
            )
            return self._make_response(reply=reply, run=run, context=context, route=route)

        raise RuntimeError(f"Unsupported control route: {route.kind}")

    def _promote_run_to_foreground(self, *, conversation_id: str, run_id: str) -> Run:
        state = self.get_conversation_state(conversation_id)
        previous_foreground = state.foreground_run_id
        if previous_foreground and previous_foreground != run_id:
            previous_run = self.get_run(previous_foreground)
            if previous_run is not None and previous_run.status in {RunStatus.created, RunStatus.running}:
                self.run_store.update_run(previous_foreground, background=True)

        promoted = self.run_store.update_run(run_id, background=False)
        self._conversation_foregrounds[conversation_id] = run_id
        return promoted

    def _refresh_foreground_after_terminal(self, conversation_id: str, terminal_run_id: str) -> None:
        current_foreground = self._conversation_foregrounds.get(conversation_id, "")
        if current_foreground != terminal_run_id:
            self.get_conversation_state(conversation_id)
            return

        snapshot = self.get_conversation_state(conversation_id)
        if snapshot.foreground_run_id:
            promoted = self.run_store.update_run(snapshot.foreground_run_id, background=False)
            self._conversation_foregrounds[conversation_id] = promoted.id
            return

        self._conversation_foregrounds.pop(conversation_id, None)

    def _pick_non_foreground_run_id(self, state: ConversationState) -> str:
        for run_id in state.background_run_ids:
            if run_id:
                return run_id
        return ""

    def _build_control_reply(self, *, route: TurnRoute, run: Run, text: str) -> str:
        _ = text
        goal = self._clip_text(run.goal or run.id, 80)
        if route.kind == TurnRouteKind.switch_run:
            return f"Switched foreground task to: {goal}"
        if route.kind == TurnRouteKind.cancel_run:
            return f"Cancelled task: {goal}"
        return goal

    def _is_run_waiting_for_client_tool(self, run_id: str) -> bool:
        state = self._pending_runs.get(run_id)
        return state is not None and bool(state.pending_tool_calls)

    def _looks_like_new_task(self, text: str) -> bool:
        value = str(text or "").strip().lower()
        if not value:
            return False

        continue_hints = ("继续", "接着", "刚才", "那个", "resume", "continue")
        if any(hint in value for hint in continue_hints):
            return False

        new_task_hints = ("另外", "再", "顺便", "同时", "还有", "另一个", "also", "another", "new task")
        return any(hint in value for hint in new_task_hints)
