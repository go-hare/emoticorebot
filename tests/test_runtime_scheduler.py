from __future__ import annotations

import asyncio
from pathlib import Path

from emoticorebot.affect import AffectState, PADVector
from emoticorebot.brain_kernel import (
    BrainOutput,
    BrainOutputType,
    BrainResponse,
    BrainTurnContext,
    Run,
)
from emoticorebot.companion import CompanionIntent, SurfaceExpression
from emoticorebot.runtime.scheduler import RuntimeScheduler


class FakeFront:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | CompanionIntent | SurfaceExpression]] = []

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
    def __init__(self, output_kind: BrainOutputType = BrainOutputType.response) -> None:
        self.output_kind = output_kind
        self.is_running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.published: list[dict[str, str]] = []
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
        event_id = f"evt_{len(self.published) + 1}"
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

    def _build_output(self, event_id: str, conversation_id: str, text: str) -> BrainOutput:
        if self.output_kind == BrainOutputType.error:
            return BrainOutput(event_id=event_id, type=BrainOutputType.error, error="kernel exploded")

        response = BrainResponse(
            reply=f"kernel raw for: {text}",
            run=Run(agent_id="alice", conversation_id=conversation_id, goal=text),
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


def test_runtime_scheduler_forwards_user_turn_into_kernel_and_front() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)
        surface_states: list[dict[str, object]] = []

        reply = await runtime.handle_user_text(
            thread_id="thread-1",
            session_id="thread-1",
            user_id="user-1",
            user_text="帮我看看日志",
            stream_handler=None,
            surface_state_handler=surface_states.append,
        )

        assert reply == "beautified reply"
        assert kernel.start_calls == 1
        assert kernel.published == [
            {
                "conversation_id": "thread-1",
                "text": "帮我看看日志",
                "user_id": "user-1",
                "turn_id": kernel.published[0]["turn_id"],
                "latest_front_reply": "",
            }
        ]
        assert kernel.published[0]["turn_id"].startswith("turn_")
        assert front.calls == [
            {
                "user_text": "帮我看看日志",
                "kernel_output": "kernel raw for: 帮我看看日志",
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

        await runtime.stop()
        assert kernel.stop_calls == 1

    asyncio.run(_exercise())


def test_runtime_scheduler_presents_kernel_errors_to_front() -> None:
    async def _exercise() -> None:
        front = FakeFront()
        kernel = FakeKernel(output_kind=BrainOutputType.error)
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=front, kernel=kernel)

        reply = await runtime.handle_user_text(
            thread_id="thread-err",
            session_id="thread-err",
            user_id="user-1",
            user_text="执行一下",
            stream_handler=None,
        )

        assert reply == "beautified reply"
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

        await runtime.stop()

    asyncio.run(_exercise())


def test_runtime_scheduler_surface_state_falls_back_to_idle_on_front_error() -> None:
    class ExplodingFront:
        async def present(
            self,
            *,
            user_text: str,
            kernel_output: str,
            companion_intent: CompanionIntent | None = None,
            surface_expression: SurfaceExpression | None = None,
            stream_handler=None,
        ) -> str:
            _ = user_text, kernel_output, companion_intent, surface_expression, stream_handler
            raise RuntimeError("front exploded")

    async def _exercise() -> None:
        kernel = FakeKernel()
        runtime = RuntimeScheduler(workspace=Path("/tmp"), front=ExplodingFront(), kernel=kernel)
        surface_states: list[dict[str, object]] = []

        reply = await runtime.handle_user_text(
            thread_id="thread-fallback",
            session_id="thread-fallback",
            user_id="user-1",
            user_text="帮我看看日志",
            stream_handler=None,
            surface_state_handler=surface_states.append,
        )

        assert reply == "kernel raw for: 帮我看看日志"
        assert [state["phase"] for state in surface_states] == ["listening", "replying", "idle"]
        assert surface_states[-1]["motion_hint"] == "minimal"
        assert runtime.get_thread_surface_state("thread-fallback") == surface_states[-1]

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

        await runtime.handle_user_text(
            thread_id="thread-affect",
            session_id="thread-affect",
            user_id="user-1",
            user_text="帮我看看日志",
            stream_handler=None,
            surface_state_handler=surface_states.append,
        )

        assert affect.calls == ["帮我看看日志"]
        assert front.calls[0]["affect_state"] == affect.state
        assert surface_states[0]["affect_pressure"] == affect.state.pressure
        assert surface_states[1]["affect_vitality"] == affect.state.vitality
        assert surface_states[-1]["affect_pleasure"] == affect.state.current_pad.pleasure

        await runtime.stop()

    asyncio.run(_exercise())
