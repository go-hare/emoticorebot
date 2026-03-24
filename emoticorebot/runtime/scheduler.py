"""Runtime bridge: front presentation wrapped around the resident brain kernel."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Any

from emoticorebot.affect import AffectRuntime, AffectState, AffectTurnResult, EmotionSignal
from emoticorebot.brain_kernel import BrainKernel, BrainOutput, BrainOutputType, MemoryView, TaskType, make_id
from emoticorebot.companion import CompanionIntent, SurfaceExpression, build_companion_surface
from emoticorebot.front.service import FrontService


@dataclass(slots=True, frozen=True)
class FrontOutputPacket:
    """One frontend-visible output event emitted by the runtime."""

    type: str
    thread_id: str
    turn_id: str
    text: str = ""
    error: str = ""

    def as_event(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if self.type == "reply_chunk":
            payload["chunk"] = self.text
        elif self.type == "reply_done":
            payload["text"] = self.text
        elif self.type == "turn_error":
            payload["error"] = self.error
        return {"type": self.type, "payload": payload}


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
        self._pending_kernel_deliveries: dict[str, dict[str, Any]] = {}
        self._delivery_tasks: set[asyncio.Task[None]] = set()
        self._active_turns: dict[str, int] = {}
        self._thread_surface_state: dict[str, dict[str, Any]] = {}
        self._front_output_subscribers: set[asyncio.Queue[FrontOutputPacket]] = set()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._listener_task is not None and not self._listener_task.done():
                return

            self._listener_error = None
            await self.kernel.start()
            self._pending_kernel_deliveries = {}
            self._delivery_tasks = set()
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

            self._pending_kernel_deliveries.clear()
            self._thread_surface_state.clear()
            self._front_output_subscribers.clear()
            self._fail_pending_outputs(RuntimeError("Runtime scheduler stopped."))
            await self._cancel_delivery_tasks()

    async def handle_user_text(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        surface_state_handler: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> str:
        _ = session_id
        self._ensure_running()
        self._raise_if_listener_failed()
        await self._mark_thread_active(thread_id)
        delivery_registered = False
        turn_id = make_id("turn")

        try:
            affect_turn = self._evolve_affect_turn(user_text)
            affect_state = affect_turn.state if affect_turn is not None else None
            emotion_signal = affect_turn.emotion_signal if affect_turn is not None else None
            await self._push_surface_state(
                thread_id,
                self._build_listening_state(
                    thread_id,
                    affect_state=affect_state,
                    emotion_signal=emotion_signal,
                ),
                surface_state_handler=surface_state_handler,
            )
            memory = self._build_front_memory_view(thread_id=thread_id, user_text=user_text)
            front_reply = await self._emit_front_reply(
                thread_id=thread_id,
                turn_id=turn_id,
                user_text=user_text,
                memory=memory,
                emotion_signal=emotion_signal,
            )
            await self._publish_initial_front_event(
                thread_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                user_text=user_text,
                front_reply=front_reply,
                emotion_signal=emotion_signal,
            )
            event_id = make_id("brain_event")
            self._pending_kernel_deliveries[event_id] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "turn_id": turn_id,
                "user_text": user_text,
                "affect_state": affect_state,
                "emotion_signal": emotion_signal,
                "surface_state_handler": surface_state_handler,
            }
            try:
                await self.kernel.publish_user_input(
                    event_id=event_id,
                    conversation_id=thread_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    text=user_text,
                    latest_front_reply=front_reply,
                )
            except Exception as exc:
                self._pending_kernel_deliveries.pop(event_id, None)
                await self._publish_turn_error(
                    thread_id=thread_id,
                    turn_id=turn_id,
                    error=str(exc),
                )
                return front_reply
            delivery_registered = True
            return front_reply
        finally:
            if not delivery_registered:
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

    def subscribe_front_outputs(self) -> asyncio.Queue[FrontOutputPacket]:
        queue: asyncio.Queue[FrontOutputPacket] = asyncio.Queue()
        self._front_output_subscribers.add(queue)
        return queue

    def unsubscribe_front_outputs(self, queue: asyncio.Queue[FrontOutputPacket]) -> None:
        self._front_output_subscribers.discard(queue)

    async def _listen_kernel_outputs(self) -> None:
        try:
            while True:
                output = await self.kernel.recv_output()
                future = self._pending_outputs.pop(output.event_id, None)
                if future is not None and not future.done():
                    future.set_result(output)
                    continue

                delivery = self._pending_kernel_deliveries.pop(output.event_id, None)
                if delivery is not None:
                    self._schedule_kernel_delivery(output=output, delivery=delivery)
                    continue

                if output.type == BrainOutputType.recorded:
                    continue

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

    async def _await_kernel_output(self, future: asyncio.Future[BrainOutput], event_id: str) -> BrainOutput:
        try:
            return await future
        finally:
            self._pending_outputs.pop(event_id, None)

    def _register_output_waiter(self, event_id: str) -> asyncio.Future[BrainOutput]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BrainOutput] = loop.create_future()
        self._pending_outputs[event_id] = future
        return future

    def _render_kernel_output(self, output: BrainOutput) -> str:
        if output.type == BrainOutputType.response and output.response is not None:
            if output.response.task_type == TaskType.none:
                return ""
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

            if output.response.run is not None:
                return str(output.response.run.result_summary or "").strip()
            return ""

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

    async def _cancel_delivery_tasks(self) -> None:
        tasks = list(self._delivery_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._delivery_tasks.clear()

    def _schedule_kernel_delivery(self, *, output: BrainOutput, delivery: dict[str, Any]) -> None:
        task = asyncio.create_task(self._deliver_kernel_output(output=output, delivery=delivery))
        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    def _raise_if_listener_failed(self) -> None:
        if self._listener_error is None:
            return
        raise RuntimeError("Brain kernel output listener failed.") from self._listener_error

    def _ensure_running(self) -> None:
        listener = self._listener_task
        if listener is not None and not listener.done() and self.kernel.is_running:
            return
        raise RuntimeError("Runtime scheduler is not running. Start it during project startup.")

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
        emotion_signal: EmotionSignal | None,
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
        state.update(self._build_emotion_payload(emotion_signal))
        return state

    def _build_idle_state(
        self,
        thread_id: str,
        *,
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None = None,
    ) -> dict[str, Any]:
        state = {
            "thread_id": thread_id,
            "phase": "idle",
            "presence": "beside",
            "motion_hint": "minimal",
            "body_state": "resting_beside",
            "breathing_hint": "soft_slow",
            "linger_hint": "quiet_stay",
            "speaking_phase": "replying",
            "settling_phase": "resting",
            "idle_phase": "idle_ready",
            "recommended_hold_ms": 0,
        }
        state.update(self._build_affect_payload(affect_state))
        state.update(self._build_emotion_payload(emotion_signal))
        return state

    def _build_surface_state(
        self,
        thread_id: str,
        *,
        phase: str,
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None,
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
        state.update(self._build_emotion_payload(emotion_signal))
        return state

    def _evolve_affect_turn(self, user_text: str) -> AffectTurnResult | None:
        if self.affect_runtime is None:
            return None
        return self.affect_runtime.evolve(user_text=user_text)

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

    def _build_emotion_payload(self, emotion_signal: EmotionSignal | None) -> dict[str, Any]:
        if emotion_signal is None:
            return {}
        payload = emotion_signal.to_dict()
        return {
            "emotion_primary": payload["primary_emotion"],
            "emotion_intensity": payload["intensity"],
            "emotion_confidence": payload["confidence"],
            "emotion_support_need": payload["support_need"],
            "emotion_wants_action": payload["wants_action"],
            "emotion_trigger_text": payload["trigger_text"],
        }

    async def _emit_front_reply(
        self,
        *,
        thread_id: str,
        turn_id: str,
        user_text: str,
        memory: MemoryView,
        emotion_signal: EmotionSignal | None,
    ) -> str:
        try:
            hint = (
                await self.front.reply(
                    user_text=user_text,
                    memory=memory,
                    emotion_signal=emotion_signal,
                    stream_handler=self._build_front_stream_handler(
                        thread_id=thread_id,
                        turn_id=turn_id,
                    ),
                )
            ).strip()
        except Exception as exc:
            await self._publish_turn_error(
                thread_id=thread_id,
                turn_id=turn_id,
                error=str(exc),
            )
            return ""
        await self._publish_reply_done(
            thread_id=thread_id,
            turn_id=turn_id,
            text=hint,
        )
        return hint

    def _build_front_memory_view(self, *, thread_id: str, user_text: str) -> MemoryView:
        memory_store = getattr(self.kernel, "memory_store", None)
        if memory_store is None:
            return MemoryView()
        try:
            return memory_store.build_memory_view(thread_id, self.kernel.agent_id, user_text)
        except Exception:
            return MemoryView()

    def _build_front_stream_handler(
        self,
        *,
        thread_id: str,
        turn_id: str,
    ) -> Callable[[str], Awaitable[None]]:
        async def _emit(chunk: str) -> None:
            text = str(chunk or "")
            if not text:
                return
            self._publish_front_output(
                FrontOutputPacket(
                    type="reply_chunk",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    text=text,
                )
            )

        return _emit

    def _publish_front_output(self, packet: FrontOutputPacket) -> None:
        for subscriber in list(self._front_output_subscribers):
            subscriber.put_nowait(packet)

    async def _publish_reply_done(self, *, thread_id: str, turn_id: str, text: str) -> None:
        final_text = str(text or "").strip()
        if not final_text:
            return
        self._publish_front_output(
            FrontOutputPacket(
                type="reply_done",
                thread_id=thread_id,
                turn_id=turn_id,
                text=final_text,
            )
        )

    async def _publish_turn_error(self, *, thread_id: str, turn_id: str, error: str) -> None:
        error_text = str(error or "").strip()
        if not error_text:
            return
        self._publish_front_output(
            FrontOutputPacket(
                type="turn_error",
                thread_id=thread_id,
                turn_id=turn_id,
                error=error_text,
            )
        )

    async def _publish_initial_front_event(
        self,
        *,
        thread_id: str,
        user_id: str,
        turn_id: str,
        user_text: str,
        front_reply: str,
        emotion_signal: EmotionSignal | None,
    ) -> None:
        if not front_reply.strip():
            return
        metadata = {
            "source": "runtime_front_hint",
        }
        metadata.update(self._build_emotion_payload(emotion_signal))
        try:
            await self.kernel.publish_front_event(
                conversation_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                front_event={
                    "event_type": "dialogue",
                    "user_text": user_text,
                    "front_reply": front_reply,
                    "emotion": emotion_signal.primary_emotion if emotion_signal is not None else "",
                    "tags": [
                        tag
                        for tag in [
                            emotion_signal.primary_emotion if emotion_signal is not None else "",
                            emotion_signal.support_need if emotion_signal is not None else "",
                        ]
                        if str(tag or "").strip()
                    ],
                    "metadata": metadata,
                },
            )
        except Exception:
            return

    async def _deliver_kernel_output(self, *, output: BrainOutput, delivery: dict[str, Any]) -> None:
        try:
            if (
                output.type == BrainOutputType.response
                and output.response is not None
                and output.response.task_type == TaskType.none
            ):
                thread_id = str(delivery["thread_id"])
                await self._push_surface_state(
                    thread_id,
                    self._build_idle_state(
                        thread_id,
                        affect_state=delivery["affect_state"],
                        emotion_signal=delivery["emotion_signal"],
                    ),
                    surface_state_handler=delivery["surface_state_handler"],
                )
                return

            kernel_output = self._render_kernel_output(output)
            if not kernel_output:
                return

            thread_id = str(delivery["thread_id"])
            user_text = str(delivery["user_text"])
            companion_intent, surface_expression = build_companion_surface(
                user_text=user_text,
                kernel_output=kernel_output,
                affect_state=delivery["affect_state"],
            )
            await self._push_surface_state(
                thread_id,
                self._build_surface_state(
                    thread_id=thread_id,
                    phase="replying",
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                surface_state_handler=delivery["surface_state_handler"],
            )

            try:
                presented = await self.front.present(
                    user_text=user_text,
                    kernel_output=kernel_output,
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                    stream_handler=self._build_front_stream_handler(
                        thread_id=thread_id,
                        turn_id=str(delivery["turn_id"]),
                    ),
                )
            except Exception as exc:
                await self._push_surface_state(
                    thread_id,
                    self._build_surface_state(
                        thread_id=thread_id,
                        phase="idle",
                        affect_state=delivery["affect_state"],
                        emotion_signal=delivery["emotion_signal"],
                        companion_intent=companion_intent,
                        surface_expression=surface_expression,
                    ),
                    surface_state_handler=delivery["surface_state_handler"],
                )
                fallback = kernel_output
                await self._publish_reply_done(
                    thread_id=thread_id,
                    turn_id=str(delivery["turn_id"]),
                    text=fallback,
                )
                await self._publish_turn_error(
                    thread_id=thread_id,
                    turn_id=str(delivery["turn_id"]),
                    error=str(exc),
                )
                return

            final_reply = presented.strip() or kernel_output
            await self._record_front_delivery(
                thread_id=thread_id,
                user_id=str(delivery["user_id"]),
                turn_id=str(delivery["turn_id"]),
                user_text=user_text,
                front_reply=final_reply,
                kernel_output=kernel_output,
                affect_state=delivery["affect_state"],
                emotion_signal=delivery["emotion_signal"],
                companion_intent=companion_intent,
                surface_expression=surface_expression,
                wait_for_record=False,
            )
            await self._push_surface_state(
                thread_id,
                self._build_surface_state(
                    thread_id=thread_id,
                    phase="settling",
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                surface_state_handler=delivery["surface_state_handler"],
            )
            await self._push_surface_state(
                thread_id,
                self._build_surface_state(
                    thread_id=thread_id,
                    phase="idle",
                    affect_state=delivery["affect_state"],
                    emotion_signal=delivery["emotion_signal"],
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                surface_state_handler=delivery["surface_state_handler"],
            )
            await self._publish_reply_done(
                thread_id=thread_id,
                turn_id=str(delivery["turn_id"]),
                text=final_reply,
            )
        finally:
            await self._mark_thread_idle(str(delivery["thread_id"]))

    async def _record_front_delivery(
        self,
        *,
        thread_id: str,
        user_id: str,
        turn_id: str,
        user_text: str,
        front_reply: str,
        kernel_output: str,
        affect_state: AffectState | None,
        emotion_signal: EmotionSignal | None,
        companion_intent: CompanionIntent,
        surface_expression: SurfaceExpression,
        wait_for_record: bool = True,
    ) -> None:
        if not front_reply.strip():
            return

        metadata: dict[str, Any] = {
            "source": "runtime_scheduler",
            "kernel_output": kernel_output,
            "mode": companion_intent.mode,
            "warmth": companion_intent.warmth,
            "initiative": companion_intent.initiative,
            "intensity": companion_intent.intensity,
            "text_style": surface_expression.text_style,
            "presence": surface_expression.presence,
            "expression": surface_expression.expression,
            "motion_hint": surface_expression.motion_hint,
        }
        metadata.update(self._build_affect_payload(affect_state))
        metadata.update(self._build_emotion_payload(emotion_signal))

        try:
            event_id = make_id("brain_event")
            future = self._register_output_waiter(event_id) if wait_for_record else None
            await self.kernel.publish_front_event(
                event_id=event_id,
                conversation_id=thread_id,
                user_id=user_id,
                turn_id=turn_id,
                front_event={
                    "event_type": "dialogue",
                    "user_text": user_text,
                    "front_reply": front_reply,
                    "emotion": surface_expression.expression,
                    "tags": [
                        tag
                        for tag in [
                            companion_intent.mode,
                            surface_expression.text_style,
                            surface_expression.presence,
                            surface_expression.expression,
                            emotion_signal.primary_emotion if emotion_signal is not None else "",
                            emotion_signal.support_need if emotion_signal is not None else "",
                        ]
                        if str(tag or "").strip()
                    ],
                    "metadata": metadata,
                },
            )
            if wait_for_record and future is not None:
                await self._await_kernel_output(future, event_id)
        except Exception:
            self._pending_outputs.pop(event_id, None)
            return
