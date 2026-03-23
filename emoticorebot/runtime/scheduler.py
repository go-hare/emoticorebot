"""Runtime bridge: front presentation wrapped around the resident brain kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from pathlib import Path
from typing import Any

from emoticorebot.affect import AffectRuntime, AffectState
from emoticorebot.brain_kernel import BrainKernel, BrainOutput, BrainOutputType, make_id
from emoticorebot.companion import CompanionIntent, SurfaceExpression, build_companion_surface
from emoticorebot.front.service import FrontService


class RuntimeScheduler:
    """Minimal outer runtime that forwards user turns into the running kernel."""

    def __init__(
        self,
        workspace: Path,
        front: FrontService,
        kernel: BrainKernel,
        affect_runtime: AffectRuntime | None = None,
    ) -> None:
        self.workspace = workspace
        self.front = front
        self.kernel = kernel
        self.affect_runtime = affect_runtime
        self._lifecycle_lock = asyncio.Lock()
        self._idle_condition = asyncio.Condition()
        self._listener_task: asyncio.Task[None] | None = None
        self._listener_error: BaseException | None = None
        self._pending_outputs: dict[str, asyncio.Future[BrainOutput]] = {}
        self._buffered_outputs: dict[str, BrainOutput] = {}
        self._active_turns: dict[str, int] = {}
        self._thread_surface_state: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._listener_task is not None and not self._listener_task.done():
                return

            self._listener_error = None
            await self.kernel.start()
            self._listener_task = asyncio.create_task(self._listen_kernel_outputs())

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            listener = self._listener_task
            if listener is None and not self.kernel.is_running:
                return

            await self.kernel.stop()
            if listener is not None:
                try:
                    await listener
                finally:
                    self._listener_task = None
            else:
                self._listener_task = None

            self._buffered_outputs.clear()
            self._thread_surface_state.clear()
            self._fail_pending_outputs(RuntimeError("Runtime scheduler stopped."))

    async def handle_user_text(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        stream_handler: Callable[[str], Awaitable[None]] | None,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> str:
        _ = session_id
        await self.start()
        self._raise_if_listener_failed()
        await self._mark_thread_active(thread_id)

        try:
            affect_state = self._evolve_affect_state(user_text)
            await self._push_surface_state(
                thread_id,
                self._build_listening_state(thread_id, affect_state=affect_state),
                surface_state_handler=surface_state_handler,
            )
            event_id = await self.kernel.publish_user_input(
                conversation_id=thread_id,
                user_id=user_id,
                turn_id=make_id("turn"),
                text=user_text,
                latest_front_reply="",
            )
            output = await self._await_kernel_output(event_id)
            kernel_output = self._render_kernel_output(output)
            if not kernel_output:
                return ""
            companion_intent, surface_expression = build_companion_surface(
                user_text=user_text,
                kernel_output=kernel_output,
                affect_state=affect_state,
            )
            await self._push_surface_state(
                thread_id,
                self._build_surface_state(
                    thread_id,
                    phase="replying",
                    affect_state=affect_state,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                surface_state_handler=surface_state_handler,
            )

            try:
                presented = await self.front.present(
                    user_text=user_text,
                    kernel_output=kernel_output,
                    affect_state=affect_state,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                    stream_handler=stream_handler,
                )
            except Exception:
                await self._push_surface_state(
                    thread_id,
                    self._build_surface_state(
                        thread_id,
                        phase="idle",
                        affect_state=affect_state,
                        companion_intent=companion_intent,
                        surface_expression=surface_expression,
                    ),
                    surface_state_handler=surface_state_handler,
                )
                return kernel_output

            await self._push_surface_state(
                thread_id,
                self._build_surface_state(
                    thread_id,
                    phase="settling",
                    affect_state=affect_state,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                surface_state_handler=surface_state_handler,
            )
            await self._push_surface_state(
                thread_id,
                self._build_surface_state(
                    thread_id,
                    phase="idle",
                    affect_state=affect_state,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                surface_state_handler=surface_state_handler,
            )
            return presented.strip() or kernel_output
        finally:
            await self._mark_thread_idle(thread_id)

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 600.0) -> None:
        async with asyncio.timeout(timeout):
            async with self._idle_condition:
                while self._active_turns.get(thread_id, 0) > 0:
                    await self._idle_condition.wait()
        self._raise_if_listener_failed()

    def get_thread_surface_state(self, thread_id: str) -> dict[str, Any] | None:
        state = self._thread_surface_state.get(thread_id)
        if state is None:
            return None
        return dict(state)

    async def _listen_kernel_outputs(self) -> None:
        try:
            while True:
                output = await self.kernel.recv_output()
                future = self._pending_outputs.pop(output.event_id, None)
                if future is not None and not future.done():
                    future.set_result(output)
                elif output.type != BrainOutputType.stopped:
                    self._buffered_outputs[output.event_id] = output

                if output.type == BrainOutputType.stopped:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._listener_error = exc
            self._fail_pending_outputs(exc)
            raise
        finally:
            if self._listener_error is None:
                self._fail_pending_outputs(RuntimeError("Brain kernel listener stopped."))
            self._listener_task = None

    async def _await_kernel_output(self, event_id: str) -> BrainOutput:
        future = self._register_output_waiter(event_id)
        try:
            return await future
        finally:
            self._pending_outputs.pop(event_id, None)

    def _register_output_waiter(self, event_id: str) -> asyncio.Future[BrainOutput]:
        buffered = self._buffered_outputs.pop(event_id, None)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrainOutput] = loop.create_future()
        if buffered is not None:
            future.set_result(buffered)
            return future

        self._pending_outputs[event_id] = future
        return future

    def _render_kernel_output(self, output: BrainOutput) -> str:
        if output.type == BrainOutputType.response and output.response is not None:
            reply = str(output.response.reply or "").strip()
            if reply:
                return reply

            if output.response.pending_tool_calls:
                tool_names = ", ".join(
                    call.tool_name for call in output.response.pending_tool_calls if call.tool_name
                ).strip()
                if tool_names:
                    return f"内核正在等待外部工具结果后继续：{tool_names}"
                return "内核正在等待外部工具结果后继续。"

            return str(output.response.run.result_summary or "").strip()

        if output.type == BrainOutputType.error:
            error = str(output.error or "").strip() or "unknown error"
            return f"内核处理失败：{error}"

        return ""

    def _fail_pending_outputs(self, error: BaseException) -> None:
        pending = list(self._pending_outputs.values())
        self._pending_outputs.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _raise_if_listener_failed(self) -> None:
        if self._listener_error is None:
            return
        raise RuntimeError("Brain kernel output listener failed.") from self._listener_error

    async def _mark_thread_active(self, thread_id: str) -> None:
        async with self._idle_condition:
            self._active_turns[thread_id] = self._active_turns.get(thread_id, 0) + 1

    async def _mark_thread_idle(self, thread_id: str) -> None:
        async with self._idle_condition:
            count = self._active_turns.get(thread_id, 0)
            if count <= 1:
                self._active_turns.pop(thread_id, None)
            else:
                self._active_turns[thread_id] = count - 1
            self._idle_condition.notify_all()

    async def _push_surface_state(
        self,
        thread_id: str,
        state: dict[str, Any],
        *,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        self._thread_surface_state[thread_id] = dict(state)
        if surface_state_handler is not None:
            maybe_awaitable = surface_state_handler(dict(state))
            if isawaitable(maybe_awaitable):
                await maybe_awaitable

    def _build_listening_state(
        self,
        thread_id: str,
        *,
        affect_state: AffectState | None,
    ) -> dict[str, Any]:
        state = {
            "thread_id": thread_id,
            "phase": "listening",
            "presence": "beside",
            "motion_hint": "small_nod",
            "body_state": "listening_beside",
            "breathing_hint": "steady_even",
            "linger_hint": "remain_available",
            "speaking_phase": "listening",
            "settling_phase": "listening",
            "idle_phase": "idle_ready",
            "recommended_hold_ms": 0,
        }
        state.update(self._build_affect_payload(affect_state))
        return state

    def _build_surface_state(
        self,
        thread_id: str,
        *,
        phase: str,
        affect_state: AffectState | None,
        companion_intent: CompanionIntent,
        surface_expression: SurfaceExpression,
    ) -> dict[str, Any]:
        recommended_hold_ms = 900 if phase == "settling" else 0
        body_state = surface_expression.body_state
        motion_hint = surface_expression.motion_hint
        lifecycle_phase = surface_expression.speaking_phase

        if phase == "settling":
            lifecycle_phase = surface_expression.settling_phase
            motion_hint = "stay_close"
        elif phase == "idle":
            lifecycle_phase = surface_expression.idle_phase
            motion_hint = "minimal"

        state = {
            "thread_id": thread_id,
            "phase": phase,
            "mode": companion_intent.mode,
            "warmth": companion_intent.warmth,
            "initiative": companion_intent.initiative,
            "intensity": companion_intent.intensity,
            "text_style": surface_expression.text_style,
            "presence": surface_expression.presence,
            "expression": surface_expression.expression,
            "motion_hint": motion_hint,
            "body_state": body_state,
            "breathing_hint": surface_expression.breathing_hint,
            "linger_hint": surface_expression.linger_hint,
            "speaking_phase": surface_expression.speaking_phase,
            "settling_phase": surface_expression.settling_phase,
            "idle_phase": surface_expression.idle_phase,
            "lifecycle_phase": lifecycle_phase,
            "recommended_hold_ms": recommended_hold_ms,
        }
        state.update(self._build_affect_payload(affect_state))
        return state

    def _evolve_affect_state(self, user_text: str) -> AffectState | None:
        if self.affect_runtime is None:
            return None
        return self.affect_runtime.evolve(user_text=user_text).state

    def _build_affect_payload(self, affect_state: AffectState | None) -> dict[str, Any]:
        if affect_state is None:
            return {}
        return {
            "affect_pleasure": affect_state.current_pad.pleasure,
            "affect_arousal": affect_state.current_pad.arousal,
            "affect_dominance": affect_state.current_pad.dominance,
            "affect_vitality": affect_state.vitality,
            "affect_pressure": affect_state.pressure,
        }
