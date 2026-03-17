from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from emoticorebot.execution.team import WorkerOutcome
from emoticorebot.io.normalizer import InputNormalizer
from emoticorebot.protocol.envelope import build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload
from emoticorebot.protocol.task_models import ReplyDraft
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.runtime.kernel import RuntimeKernel


class _FakeBrainLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any):
        self.prompts.append(prompt)
        return self.response


class _StreamingBrainLLM:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.prompts: list[Any] = []

    async def astream(self, prompt: Any):
        self.prompts.append(prompt)
        for chunk in self.chunks:
            yield chunk


class _QueuedBrainLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("queued brain llm ran out of scripted responses")
        return self.responses.pop(0)


class _FakeContextBuilder:
    def build_brain_decision_system_prompt(self, *, query: str = "") -> str:
        return f"decision system for: {query}"

    def build_brain_system_prompt(self, *, query: str = "") -> str:
        return f"full system for: {query}"


def _brain_packet(
    *,
    task_action: str = "none",
    final_decision: str = "answer",
    final_message: str,
    task_reason: str = "",
    task: dict[str, object] | None = None,
) -> str:
    lines = [
        "####user####",
        final_message,
        "",
        "####task####",
        f"mode={final_decision}",
        f"action={task_action}",
    ]
    task_payload = task or {}
    if task_reason:
        lines.append(f"reason={task_reason}")
    for key in ("task_id", "title", "request", "goal", "expected_output", "history_context", "review_policy", "preferred_agent"):
        value = str(task_payload.get(key, "") or "").strip()
        if value:
            lines.append(f"{key}={value}")
    for key in ("constraints", "success_criteria", "memory_refs", "skill_hints"):
        values = [str(item).strip() for item in list(task_payload.get(key, []) or []) if str(item).strip()]
        if values:
            lines.append(f"{key}={'|'.join(values)}")
    return "\n".join(lines) + "\n"


async def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


async def _exercise_kernel_direct_reply() -> None:
    transport = TransportBus()
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport)
        try:
            with pytest.raises(RuntimeError, match="brain_llm"):
                await kernel.handle_user_message(
                    session_id="cli:direct",
                    channel="cli",
                    chat_id="direct",
                    sender_id="user",
                    message_id="msg_hello",
                    content="你好",
                )
        finally:
            await kernel.stop()


def test_runtime_kernel_requires_brain_llm_for_user_turn() -> None:
    asyncio.run(_exercise_kernel_direct_reply())


async def _exercise_kernel_task_flow() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="create_task",
            final_decision="continue",
            final_message="已接收，开始处理。",
            task_reason="需要执行代码修改任务",
            task={
                "title": "修改 add.py",
                "request": "修改 add.py，让 add(a, b) 返回 a + b",
                "review_policy": "skip",
                "preferred_agent": "worker",
            },
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:direct",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_task",
                content="修改 add.py，让 add(a, b) 返回 a + b",
            )
            assert "开始处理" in reply.content

            await _wait_for(lambda: transport.outbound_size >= 2, timeout=2.0)
            first = await transport.consume_outbound()
            second = await transport.consume_outbound()

            assert "开始处理" in first.content
            assert "已完成" in second.content
            latest_task = kernel.latest_task_for_session("cli:direct", include_terminal=True)
            assert latest_task is not None
            assert latest_task.state.value in {"done", "running"}
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_task_flow() -> None:
    asyncio.run(_exercise_kernel_task_flow())


async def _exercise_kernel_minimal_task_flow() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        """####user####
好的，我来处理。

####task####
mode=continue
action=create_task
"""
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:minimal",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_minimal_task",
                content="创建一个 add.py 文件 add(a, b) 返回 a + b",
            )
            assert "好的" in reply.content

            await _wait_for(lambda: transport.outbound_size >= 2, timeout=2.0)
            first = await transport.consume_outbound()
            second = await transport.consume_outbound()

            assert "好的" in first.content
            assert "已完成" in second.content
            latest_task = kernel.latest_task_for_session("cli:minimal", include_terminal=True)
            assert latest_task is not None
            assert latest_task.request.request == "创建一个 add.py 文件 add(a, b) 返回 a + b"
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_minimal_brain_packet_task_flow() -> None:
    asyncio.run(_exercise_kernel_minimal_task_flow())


async def _exercise_kernel_falls_back_when_create_task_reply_is_empty() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        {
            "task_action": "create_task",
            "task_reason": "需要执行文件创建任务",
            "final_decision": "continue",
            "final_message": "",
            "task": {
                "title": "创建 add.py",
                "request": "创建 add.py，让 add(a, b) 返回 a + b",
                "review_policy": "skip",
                "preferred_agent": "worker",
            },
        }
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:fallback-task",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_fallback_task",
                content="创建一个 add.py 文件 add(a, b) 返回 a + b",
            )
            assert reply.content == "收到，我开始处理。"
        finally:
            await kernel.stop()


def test_runtime_kernel_falls_back_when_create_task_reply_is_empty() -> None:
    asyncio.run(_exercise_kernel_falls_back_when_create_task_reply_is_empty())


async def _exercise_kernel_persona_rollback() -> None:
    transport = TransportBus()
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        kernel = RuntimeKernel(workspace=workspace, transport=transport)
        try:
            kernel.reflection_runtime.persona.apply_updates_result("persona", ["先判断再执行"], scope="deep")
            kernel.reflection_runtime.persona.apply_updates_result("persona", ["结论优先返回"], scope="deep")

            result = await kernel.rollback_persona(
                target="persona",
                scope="deep",
                version=1,
                session_id="sess_admin",
                turn_id="turn_admin",
                correlation_id="admin_rollback",
                reason="manual_fix",
            )
            await kernel._bus.drain()

            assert result.applied is True
            assert result.rollback_to_version == 1
            soul = (workspace / "SOUL.md").read_text(encoding="utf-8")
            assert "先判断再执行" in soul
            assert "结论优先返回" not in soul
        finally:
            await kernel.stop()


def test_runtime_kernel_exposes_persona_rollback() -> None:
    asyncio.run(_exercise_kernel_persona_rollback())


async def _exercise_kernel_direct_reply_via_llm() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="none",
            final_decision="answer",
            final_message="1 + 1 = 2",
            task_reason="simple_math_answer",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:direct",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_math",
                content="1 + 1 = ?",
            )
            assert reply.content == "1 + 1 = 2"
            assert brain_llm.prompts
            outbound = await transport.consume_outbound()
            assert outbound.content == "1 + 1 = 2"
        finally:
            await kernel.stop()


def test_runtime_kernel_uses_brain_llm_for_direct_reply() -> None:
    asyncio.run(_exercise_kernel_direct_reply_via_llm())


async def _exercise_kernel_suppresses_direct_reply_delivery() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="none",
            final_decision="answer",
            final_message="1 + 1 = 2",
            task_reason="simple_math_answer",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:suppressed",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_math_suppressed",
                content="1 + 1 = ?",
                metadata={"suppress_delivery": True},
            )
            assert reply.content == "1 + 1 = 2"
            await asyncio.sleep(0.05)
            assert transport.outbound_size == 0
        finally:
            await kernel.stop()


def test_runtime_kernel_suppresses_direct_reply_delivery() -> None:
    asyncio.run(_exercise_kernel_suppresses_direct_reply_delivery())


async def _exercise_kernel_brain_uses_system_and_user_messages() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="none",
            final_decision="answer",
            final_message="收到",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        try:
            await kernel.handle_user_message(
                session_id="cli:messages",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_messages",
                content="你好",
            )
            assert len(brain_llm.prompts) == 1
            messages = brain_llm.prompts[0]
            assert isinstance(messages, list)
            assert len(messages) == 2
            assert getattr(messages[0], "type", "") == "system"
            assert getattr(messages[1], "type", "") == "human"
            assert "full system for: 你好" in str(getattr(messages[0], "content", ""))
            assert "decision system for: 你好" not in str(getattr(messages[0], "content", ""))
            assert "## 当前轮执行要求" in str(getattr(messages[1], "content", ""))
            assert "## 用户消息" in str(getattr(messages[1], "content", ""))
        finally:
            await kernel.stop()


def test_runtime_kernel_brain_uses_system_and_user_messages() -> None:
    asyncio.run(_exercise_kernel_brain_uses_system_and_user_messages())


async def _exercise_kernel_streams_cli_reply_before_final_packet() -> None:
    transport = TransportBus()
    brain_llm = _StreamingBrainLLM(
        [
            "####user####\n你好。",
            "\n\n####task####\nmode=answer\naction=none\n",
        ]
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
        )
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:stream",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_stream",
                content="你好",
            )
            assert reply.content == "你好。"

            await _wait_for(lambda: transport.outbound_size >= 2)
            first = await transport.consume_outbound()
            second = await transport.consume_outbound()

            assert first.metadata["_stream"] is True
            assert first.metadata["_stream_state"] == "open"
            assert first.content == "你好。"
            assert second.metadata["_stream"] is True
            assert second.metadata["_stream_state"] == "close"
            assert second.content == "你好。"
        finally:
            await kernel.stop()


def test_runtime_kernel_streams_cli_reply_before_final_packet() -> None:
    asyncio.run(_exercise_kernel_streams_cli_reply_before_final_packet())


async def _exercise_kernel_streams_channel_reply_before_final_packet() -> None:
    transport = TransportBus()
    brain_llm = _StreamingBrainLLM(
        [
            "####user####\n好的，正在处理。",
            "\n\n####task####\nmode=continue\naction=create_task\n",
        ]
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
        )
        try:
            reply = await kernel.handle_user_message(
                session_id="telegram:stream",
                channel="telegram",
                chat_id="123456",
                sender_id="user",
                message_id="msg_stream_telegram",
                content="创建一个 add.py 文件 add(a, b) 返回 a + b",
            )
            assert "正在处理" in reply.content

            await _wait_for(lambda: transport.outbound_size >= 3, timeout=2.0)
            first = await transport.consume_outbound()
            second = await transport.consume_outbound()
            third = await transport.consume_outbound()

            assert first.metadata["_stream"] is True
            assert first.metadata["_stream_state"] == "open"
            assert "正在处理" in first.content
            assert second.metadata["_stream"] is True
            assert second.metadata["_stream_state"] == "close"
            assert "正在处理" in second.content
            assert "已完成" in third.content
        finally:
            await kernel.stop()


def test_runtime_kernel_streams_channel_reply_before_task_result() -> None:
    asyncio.run(_exercise_kernel_streams_channel_reply_before_final_packet())


async def _exercise_kernel_suppresses_task_flow_delivery() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="create_task",
            final_decision="continue",
            final_message="已接收，开始处理。",
            task_reason="需要执行代码修改任务",
            task={
                "title": "修改 add.py",
                "request": "修改 add.py，让 add(a, b) 返回 a + b",
                "review_policy": "skip",
                "preferred_agent": "worker",
            },
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:suppressed-task",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_task_suppressed",
                content="修改 add.py，让 add(a, b) 返回 a + b",
                metadata={"suppress_delivery": True},
            )
            assert "开始处理" in reply.content

            def _task_completed() -> bool:
                task = kernel.latest_task_for_session("cli:suppressed-task", include_terminal=True)
                return task is not None and task.state.value == "done"

            await _wait_for(_task_completed)
            assert transport.outbound_size == 0
        finally:
            await kernel.stop()


def test_runtime_kernel_suppresses_task_flow_delivery() -> None:
    asyncio.run(_exercise_kernel_suppresses_task_flow_delivery())


async def _exercise_kernel_interrupt_keeps_background_task_running() -> None:
    transport = TransportBus()
    brain_llm = _QueuedBrainLLM(
        [
            _brain_packet(
                task_action="create_task",
                final_decision="continue",
                final_message="已接收，开始处理。",
                task_reason="需要执行代码修改任务",
                task={
                    "title": "修改 add.py",
                    "request": "修改 add.py，让 add(a, b) 返回 a + b",
                    "review_policy": "skip",
                    "preferred_agent": "worker",
                },
            ),
            _brain_packet(
                task_action="none",
                final_decision="answer",
                final_message="我还在处理前一个任务，同时继续回应你。",
            ),
        ]
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        worker_started = asyncio.Event()

        async def _delayed_execute(
            self,
            *,
            task,
            assignment_id,
            session_id,
            turn_id,
            correlation_id,
            resume_input=None,
        ):
            del assignment_id, session_id, turn_id, correlation_id, resume_input
            worker_started.set()
            await asyncio.sleep(0.1)
            return WorkerOutcome(
                status="result",
                summary=f"{task.title} 已完成",
                result_text=f"{task.title} 已完成",
                confidence=0.9,
            )

        worker = kernel.task_runtime.worker
        worker._execute = _delayed_execute.__get__(worker, type(worker))

        try:
            first = await kernel.handle_user_message(
                session_id="cli:keep-task",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_first",
                content="修改 add.py，让 add(a, b) 返回 a + b",
            )
            assert "开始处理" in first.content

            await asyncio.wait_for(worker_started.wait(), timeout=1.0)
            second = await kernel.handle_user_message(
                session_id="cli:keep-task",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_second",
                content="你还在吗？",
            )
            assert "继续回应" in second.content

            def _task_completed() -> bool:
                latest = kernel.latest_task_for_session("cli:keep-task", include_terminal=True)
                return latest is not None and latest.latest_result is not None

            await _wait_for(_task_completed, timeout=1.5)
            latest_task = kernel.latest_task_for_session("cli:keep-task", include_terminal=True)
            assert latest_task is not None
            assert latest_task.latest_result is not None
            assert latest_task.state.value == "done"
            assert latest_task.error == ""
        finally:
            await kernel.stop()


def test_runtime_kernel_new_input_keeps_background_task_running() -> None:
    asyncio.run(_exercise_kernel_interrupt_keeps_background_task_running())


class _CapturingNormalizer(InputNormalizer):
    def __init__(self) -> None:
        self.last_barge_in: bool | None = None

    def normalize_text_message(self, **kwargs):
        self.last_barge_in = bool(kwargs.get("barge_in"))
        return super().normalize_text_message(**kwargs)


async def _exercise_kernel_marks_new_input_as_barge_in_when_stream_is_active() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="none",
            final_decision="answer",
            final_message="收到，你继续说。",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(workspace=Path(tmp_dir), transport=transport, brain_llm=brain_llm)
        kernel._input_normalizer = _CapturingNormalizer()
        try:
            await kernel.start()
            await kernel._bus.publish(
                build_envelope(
                    event_type=EventType.OUTPUT_REPLY_APPROVED,
                    source="guard",
                    target="broadcast",
                    session_id="cli:barge",
                    turn_id="turn_stream",
                    correlation_id="turn_stream",
                    payload=ReplyReadyPayload(
                        reply=ReplyDraft(
                            reply_id="reply_stream_open",
                            kind="answer",
                            plain_text="你",
                            metadata={"stream_id": "stream_turn_stream", "stream_state": "open"},
                        )
                    ),
                )
            )
            await kernel._bus.drain()

            reply = await kernel.handle_user_message(
                session_id="cli:barge",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_barge",
                content="打断一下",
            )
            assert reply.content == "收到，你继续说。"
            assert kernel._input_normalizer.last_barge_in is True
        finally:
            await kernel.stop()


def test_runtime_kernel_marks_new_input_as_barge_in_when_stream_is_active() -> None:
    asyncio.run(_exercise_kernel_marks_new_input_as_barge_in_when_stream_is_active())
