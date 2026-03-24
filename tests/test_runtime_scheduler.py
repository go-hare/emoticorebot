from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from emoticorebot.affect import AffectState, PADVector
from emoticorebot.brain_kernel import (
    BrainOutput,
    BrainOutputType,
    BrainResponse,
    BrainTurnContext,
    MemoryView,
    Run,
    TaskType,
)
from emoticorebot.companion import CompanionIntent, SurfaceExpression
from emoticorebot.runtime.scheduler import FrontOutputPacket, RuntimeScheduler


class FakeFront:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | CompanionIntent | SurfaceExpression]] = []
        self.reply_calls: list[dict[str, object]] = []

    async def reply(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        stream_handler=None,
    ) -> str:
        if stream_handler is not None:
            await stream_handler("front live hint")
        self.reply_calls.append(
            {
                "user_text": user_text,
                "memory": memory,
            }
        )
        return "front live hint"

    async def present(
        self,
        *,
        user_text: str,
        kernel_output: str,
        affect_state: AffectState | None = None,
        companion_intent: CompanionIntent | None = None,
        surface_expression: SurfaceExpression | None = None,
        stream_handler=None,
    ) -> str:
        self.calls.append(
            {
                "user_text": user_text,
                "kernel_output": kernel_output,
                "affect_state": affect_state,
                "companion_intent": companion_intent,
                "surface_expression": surface_expression,
            }
        )
        if stream_handler is not None:
            await stream_handler("beautified reply")
        return "beautified reply"


class FakeKernel:
    def __init__(self, output_kind: BrainOutputType = BrainOutputType.response, task_type: TaskType = TaskType.simple) -> None:
        self.output_kind = output_kind
        self.task_type = task_type
        self.is_running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.published: list[dict[str, str]] = []
        self.front_events: list[dict[str, object]] = []
        self.output_queue: asyncio.Queue[BrainOutput] = asyncio.Queue()

    async def start(self) -> None:
        self.start_calls += 1
        self.is_running = True

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.stop_calls += 1
        self.is_running = False
        await self.output_queue.put(BrainOutput(event_id="stop_evt", type=BrainOutputType.stopped))

    async def publish_user_input(
        self,
        *,
        event_id: str = "",
        conversation_id: str,
        text: str,
        user_id: str = "",
        turn_id: str = "",
        latest_front_reply: str = "",
        background=None,
        target_run_id: str = "",
        metadata=None,
    ) -> str:
        _ = background, target_run_id, metadata
        event_id = event_id or f"evt_{len(self.published) + 1}"
        self.published.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                "user_id": user_id,
                "turn_id": turn_id,
                "latest_front_reply": latest_front_reply,
            }
        )
        await self.output_queue.put(self._build_output(event_id, conversation_id, text))
        await asyncio.sleep(0)
        return event_id

    async def recv_output(self) -> BrainOutput:
        return await self.output_queue.get()

    async def publish_front_event(
        self,
        *,
        event_id: str = "",
        conversation_id: str,
        front_event,
        user_id: str = "",
        turn_id: str = "",
    ) -> str:
        event_id = event_id or f"front_evt_{len(self.front_events) + 1}"
        self.front_events.append(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "turn_id": turn_id,
                "front_event": front_event,
            }
        )
        await self.output_queue.put(BrainOutput(event_id=event_id, type=BrainOutputType.recorded))
        await asyncio.sleep(0)
        return event_id

    def _build_output(self, event_id: str, conversation_id: str, text: str) -> BrainOutput:
        if self.output_kind == BrainOutputType.error:
            return BrainOutput(event_id=event_id, type=BrainOutputType.error, error="kernel exploded")

        response = BrainResponse(
            task_type=self.task_type,
            reply=f"kernel raw for: {text}",
            run=None if self.task_type == TaskType.none else Run(agent_id="alice", conversation_id=conversation_id, goal=text),
            context=BrainTurnContext(
                agent_id="alice",
                conversation_id=conversation_id,
                input_kind="user",
                input_text=text,
                core_memory="",
            ),
        )
        return BrainOutput(event_id=event_id, type=BrainOutputType.response, response=response)


class FakeAffectRuntime:
    def __init__(self, state: AffectState | None = None) -> None:
        self.calls: list[str] = []
        self.state = state or AffectState(
            current_pad=PADVector(pleasure=-0.28, arousal=0.34, dominance=-0.12),
            last_user_pad=PADVector(pleasure=-0.30, arousal=0.26, dominance=-0.22),
            last_delta_pad=PADVector(pleasure=-0.08, arousal=0.12, dominance=-0.04),
            vitality=0.31,
            pressure=0.52,
            turn_count=4,
            updated_at="2026-03-23T23:58:00",
        )

    def evolve(self, *, user_text: str):
        self.calls.append(user_text)

        class Result:
            def __init__(self, state: AffectState) -> None:
                self.state = state

        return Result(self.state)


def _drain_front_packets(queue: asyncio.Queue[FrontOutputPacket]) -> list[FrontOutputPacket]:
    packets: list[FrontOutputPacket] = []
    while True:
        try:
            packets.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return packets


def test_runtime_scheduler_forwards_user_turn_into_kernel_and_front() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)
        surface_states: list[dict[str, object]] = []
        output_queue = runtime.subscribe_front_outputs()
        await runtime.start()

        reply = await runtime.handle_user_text(
            thread_id="thread-1",
            session_id="thread-1",
            user_id="user-1",
            user_text="帮我看看日志",
            surface_state_handler=surface_states.append,
        )
        await runtime.wait_for_thread_idle("thread-1", timeout=1.0)
        packets = _drain_front_packets(output_queue)

        assert reply == "front live hint"
        assert [(packet.type, packet.text or packet.error) for packet in packets] == [
            ("reply_chunk", "front live hint"),
            ("reply_done", "front live hint"),
            ("reply_chunk", "beautified reply"),
            ("reply_done", "beautified reply"),
        ]
        assert {packet.turn_id for packet in packets} == {kernel.published[0]["turn_id"]}
        assert kernel.start_calls == 1
        assert kernel.published == [
            {
                "conversation_id": "thread-1",
                "text": "帮我看看日志",
                "user_id": "user-1",
                "turn_id": kernel.published[0]["turn_id"],
                "latest_front_reply": "front live hint",
            }
        ]
        assert kernel.published[0]["turn_id"].startswith("turn_")
        assert front.reply_calls == [{"user_text": "帮我看看日志", "memory": MemoryView()}]
        assert front.calls == [
            {
                "user_text": "帮我看看日志",
                "kernel_output": "kernel raw for: 帮我看看日志",
                "affect_state": None,
                "companion_intent": front.calls[0]["companion_intent"],
                "surface_expression": front.calls[0]["surface_expression"],
            }
        ]
        assert kernel.front_events == [
            {
                "conversation_id": "thread-1",
                "user_id": "user-1",
                "turn_id": kernel.front_events[0]["turn_id"],
                "front_event": {
                    "event_type": "dialogue",
                    "user_text": "帮我看看日志",
                    "front_reply": "front live hint",
                    "metadata": {
                        "source": "runtime_front_hint",
                    },
                },
            },
            {
                "conversation_id": "thread-1",
                "user_id": "user-1",
                "turn_id": kernel.front_events[0]["turn_id"],
                "front_event": {
                    "event_type": "dialogue",
                    "user_text": "帮我看看日志",
                    "front_reply": "beautified reply",
                        "emotion": "attentive_warm",
                        "tags": ["focused", "warm_clear", "beside", "attentive_warm"],
                        "metadata": {
                            "source": "runtime_scheduler",
                            "kernel_output": "kernel raw for: 帮我看看日志",
                            "mode": "focused",
                            "warmth": kernel.front_events[1]["front_event"]["metadata"]["warmth"],
                            "initiative": kernel.front_events[1]["front_event"]["metadata"]["initiative"],
                            "intensity": kernel.front_events[1]["front_event"]["metadata"]["intensity"],
                            "text_style": "warm_clear",
                            "presence": "beside",
                            "expression": "attentive_warm",
                            "motion_hint": "small_nod",
                    },
                },
            }
        ]
        assert str(kernel.front_events[0]["turn_id"]).startswith("turn_")
        intent = front.calls[0]["companion_intent"]
        expression = front.calls[0]["surface_expression"]
        assert isinstance(intent, CompanionIntent)
        assert intent.mode == "focused"
        assert intent.warmth >= 0.80
        assert isinstance(expression, SurfaceExpression)
        assert expression.motion_hint == "small_nod"
        assert [state["phase"] for state in surface_states] == [
            "listening",
            "replying",
            "settling",
            "idle",
        ]
        assert surface_states[0]["body_state"] == "listening_beside"
        assert surface_states[-1]["lifecycle_phase"] == "idle_ready"
        assert runtime.get_thread_surface_state("thread-1") == surface_states[-1]

        runtime.unsubscribe_front_outputs(output_queue)
        await runtime.stop()
        assert kernel.stop_calls == 1

    asyncio.run(_exercise())


def test_runtime_scheduler_handle_user_text_emits_two_reply_done_events() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)
        output_queue = runtime.subscribe_front_outputs()
        await runtime.start()

        final_reply = await runtime.handle_user_text(
            thread_id="thread-front",
            session_id="thread-front",
            user_id="user-1",
            user_text="帮我看看日志",
        )
        await runtime.wait_for_thread_idle("thread-front", timeout=1.0)
        packets = _drain_front_packets(output_queue)

        assert final_reply == "front live hint"
        assert [packet.type for packet in packets] == [
            "reply_chunk",
            "reply_done",
            "reply_chunk",
            "reply_done",
        ]
        assert [packet.text for packet in packets if packet.type == "reply_done"] == [
            "front live hint",
            "beautified reply",
        ]
        assert kernel.published == [
            {
                "conversation_id": "thread-front",
                "text": "帮我看看日志",
                "user_id": "user-1",
                "turn_id": kernel.published[0]["turn_id"],
                "latest_front_reply": "front live hint",
            }
        ]
        assert front.reply_calls == [{"user_text": "帮我看看日志", "memory": MemoryView()}]
        assert front.calls == [
            {
                "user_text": "帮我看看日志",
                "kernel_output": "kernel raw for: 帮我看看日志",
                "affect_state": None,
                "companion_intent": front.calls[0]["companion_intent"],
                "surface_expression": front.calls[0]["surface_expression"],
            }
        ]
        assert [event["front_event"]["front_reply"] for event in kernel.front_events] == [
            "front live hint",
            "beautified reply",
        ]

        runtime.unsubscribe_front_outputs(output_queue)
        await runtime.stop()

    asyncio.run(_exercise())


def test_runtime_scheduler_presents_kernel_errors_to_front() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel(output_kind=BrainOutputType.error)
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)
        output_queue = runtime.subscribe_front_outputs()
        await runtime.start()

        reply = await runtime.handle_user_text(
            thread_id="thread-err",
            session_id="thread-err",
            user_id="user-1",
            user_text="执行一下",
        )
        await runtime.wait_for_thread_idle("thread-err", timeout=1.0)
        packets = _drain_front_packets(output_queue)

        assert reply == "front live hint"
        assert [(packet.type, packet.text or packet.error) for packet in packets] == [
            ("reply_chunk", "front live hint"),
            ("reply_done", "front live hint"),
            ("reply_chunk", "beautified reply"),
            ("reply_done", "beautified reply"),
        ]
        assert front.reply_calls == [{"user_text": "执行一下", "memory": MemoryView()}]
        assert kernel.published[0]["latest_front_reply"] == "front live hint"
        assert front.calls == [
            {
                "user_text": "执行一下",
                "kernel_output": "内核处理失败：kernel exploded",
                "affect_state": None,
                "companion_intent": front.calls[0]["companion_intent"],
                "surface_expression": front.calls[0]["surface_expression"],
            }
        ]
        intent = front.calls[0]["companion_intent"]
        expression = front.calls[0]["surface_expression"]
        assert isinstance(intent, CompanionIntent)
        assert intent.mode == "focused"
        assert intent.warmth >= 0.80
        assert isinstance(expression, SurfaceExpression)
        assert expression.text_style == "warm_clear"

        runtime.unsubscribe_front_outputs(output_queue)
        await runtime.stop()

    asyncio.run(_exercise())


def test_runtime_scheduler_surface_state_falls_back_to_idle_on_front_error() -> None:
    class ExplodingFront(FakeFront):
        async def present(
            self,
            *,
            user_text: str,
            kernel_output: str,
            affect_state: AffectState | None = None,
            companion_intent: CompanionIntent | None = None,
            surface_expression: SurfaceExpression | None = None,
            stream_handler=None,
        ) -> str:
            _ = (
                user_text,
                kernel_output,
                affect_state,
                companion_intent,
                surface_expression,
                stream_handler,
            )
            raise RuntimeError("front exploded")

    async def _exercise() -> None:
        kernel = FakeKernel()
        front = ExplodingFront()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)
        surface_states: list[dict[str, object]] = []
        output_queue = runtime.subscribe_front_outputs()
        await runtime.start()

        reply = await runtime.handle_user_text(
            thread_id="thread-fallback",
            session_id="thread-fallback",
            user_id="user-1",
            user_text="帮我看看日志",
            surface_state_handler=surface_states.append,
        )
        await runtime.wait_for_thread_idle("thread-fallback", timeout=1.0)
        packets = _drain_front_packets(output_queue)

        assert reply == "front live hint"
        assert [(packet.type, packet.text or packet.error) for packet in packets] == [
            ("reply_chunk", "front live hint"),
            ("reply_done", "front live hint"),
            ("reply_done", "kernel raw for: 帮我看看日志"),
            ("turn_error", "front exploded"),
        ]
        assert front.reply_calls == [{"user_text": "帮我看看日志", "memory": MemoryView()}]
        assert [state["phase"] for state in surface_states] == ["listening", "replying", "idle"]
        assert surface_states[-1]["motion_hint"] == "minimal"
        assert runtime.get_thread_surface_state("thread-fallback") == surface_states[-1]

        runtime.unsubscribe_front_outputs(output_queue)
        await runtime.stop()

    asyncio.run(_exercise())


def test_runtime_scheduler_discards_none_task_outputs_after_front_hint() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel(task_type=TaskType.none)
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)
        surface_states: list[dict[str, object]] = []
        output_queue = runtime.subscribe_front_outputs()
        await runtime.start()

        reply = await runtime.handle_user_text(
            thread_id="thread-memory-only",
            session_id="thread-memory-only",
            user_id="user-1",
            user_text="以后叫我阿青",
            surface_state_handler=surface_states.append,
        )
        await runtime.wait_for_thread_idle("thread-memory-only", timeout=1.0)
        packets = _drain_front_packets(output_queue)

        assert reply == "front live hint"
        assert [(packet.type, packet.text or packet.error) for packet in packets] == [
            ("reply_chunk", "front live hint"),
            ("reply_done", "front live hint"),
        ]
        assert front.calls == []
        assert kernel.front_events == [
            {
                "conversation_id": "thread-memory-only",
                "user_id": "user-1",
                "turn_id": kernel.front_events[0]["turn_id"],
                "front_event": {
                    "event_type": "dialogue",
                    "user_text": "以后叫我阿青",
                    "front_reply": "front live hint",
                    "metadata": {
                        "source": "runtime_front_hint",
                    },
                },
            }
        ]
        assert [state["phase"] for state in surface_states] == ["listening", "idle"]
        assert runtime.get_thread_surface_state("thread-memory-only") == surface_states[-1]

        runtime.unsubscribe_front_outputs(output_queue)
        await runtime.stop()

    asyncio.run(_exercise())


def test_runtime_scheduler_start_and_stop_are_idempotent() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)

        await runtime.start()
        await runtime.start()
        await runtime.stop()
        await runtime.stop()

        assert kernel.start_calls == 1
        assert kernel.stop_calls == 1

    asyncio.run(_exercise())


def test_runtime_scheduler_requires_explicit_start_before_turns() -> None:
    async def _exercise() -> None:
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=FakeFront(), kernel=FakeKernel())

        with pytest.raises(RuntimeError, match="Runtime scheduler is not running"):
            await runtime.handle_user_text(
                thread_id="thread-unstarted",
                session_id="thread-unstarted",
                user_id="user-1",
                user_text="帮我看看日志",
            )

    asyncio.run(_exercise())


def test_runtime_scheduler_passes_affect_state_into_front_and_surface() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel()
        affect = FakeAffectRuntime()
        runtime = RuntimeScheduler(
            workspace=Path("/tmp"),
            front=front,
            kernel=kernel,
            affect_runtime=affect,
        )
        surface_states: list[dict[str, object]] = []
        await runtime.start()

        await runtime.handle_user_text(
            thread_id="thread-affect",
            session_id="thread-affect",
            user_id="user-1",
            user_text="帮我看看日志",
            surface_state_handler=surface_states.append,
        )
        await runtime.wait_for_thread_idle("thread-affect", timeout=1.0)

        assert affect.calls == ["帮我看看日志"]
        assert front.reply_calls[0]["memory"] == MemoryView()
        assert kernel.published[0]["latest_front_reply"] == "front live hint"
        assert front.calls[0]["affect_state"] == affect.state
        assert surface_states[0]["affect_pressure"] == affect.state.pressure
        assert surface_states[1]["affect_vitality"] == affect.state.vitality
        assert surface_states[-1]["affect_pleasure"] == affect.state.current_pad.pleasure

        await runtime.stop()

    asyncio.run(_exercise())
