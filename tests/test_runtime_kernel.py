from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from emoticorebot.runtime.kernel import RuntimeKernel
from emoticorebot.runtime.transport_bus import TransportBus


class _FakeBrainLLM:
    def __init__(self, response: str | list[str]) -> None:
        if isinstance(response, list):
            self._responses = list(response)
        else:
            self._responses = [response]
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("unexpected extra brain invocation")
        return self._responses.pop(0)


class _SlowBrainLLM:
    def __init__(self, response: str, *, delay_s: float) -> None:
        self.response = response
        self.delay_s = delay_s

    async def ainvoke(self, _prompt: Any):
        await asyncio.sleep(self.delay_s)
        return self.response


class _FakeContextBuilder:
    def build_brain_decision_system_prompt(self, *, query: str = "") -> str:
        return f"decision system for: {query}"

    def build_brain_system_prompt(self, *, query: str = "") -> str:
        return f"full system for: {query}"


class _ExecutorStub:
    def __init__(self) -> None:
        self.last_task_spec: dict[str, Any] | None = None

    async def execute(self, task_spec, *, task_id: str):
        self.last_task_spec = dict(task_spec)
        del task_id
        return {
            "control_state": "completed",
            "status": "success",
            "analysis": "整理完成",
            "message": "产物已生成",
            "task_trace": [],
        }


def _brain_packet(*, final_message: str, actions: Any | None = None) -> str:
    action_payload = actions if actions is not None else {"type": "none"}
    lines = [
        "#####user######",
        final_message,
        "",
        "#####Action######",
        json.dumps(action_payload, ensure_ascii=False),
    ]
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
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            final_message="你好，我在。",
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
            reply = await kernel.handle_user_message(
                session_id="cli:direct",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_direct",
                content="你好",
            )

            assert reply.content == "你好，我在。"
            assert transport.outbound_size == 1
            outbound = await transport.consume_outbound()
            assert outbound.content == "你好，我在。"
        finally:
            await kernel.stop()


def test_runtime_kernel_handles_direct_reply() -> None:
    asyncio.run(_exercise_kernel_direct_reply())


async def _exercise_kernel_rejects_user_only_reply_block() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        """####user####
等于 2 呀……你是在故意逗我玩吗？
"""
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        try:
            with pytest.raises(RuntimeError, match="#####user###### and #####Action######"):
                await kernel.handle_user_message(
                    session_id="cli:direct",
                    channel="cli",
                    chat_id="direct",
                    sender_id="user",
                    message_id="msg_direct_partial",
                    content="1+1 = ?",
                    timeout_s=1.0,
                )
            assert transport.outbound_size == 0
        finally:
            await kernel.stop()


def test_runtime_kernel_rejects_user_only_reply_block() -> None:
    asyncio.run(_exercise_kernel_rejects_user_only_reply_block())


async def _exercise_kernel_async_executor_flow() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        [
            _brain_packet(
                final_message="已接收，开始处理。",
                actions={
                    "type": "execute",
                    "task_id": "new",
                    "goal": "整理项目结构",
                    "current_checks": ["帮我整理一下项目结构"],
                },
            ),
            _brain_packet(
                final_message="产物已生成。",
            ),
        ]
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        kernel.executor_runtime._executor = _ExecutorStub()
        try:
            reply = await kernel.handle_user_message(
                session_id="cli:right-flow",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_right_flow",
                content="帮我整理一下项目结构",
            )

            assert "开始处理" in reply.content

            def _task_completed() -> bool:
                task = kernel.latest_task_for_session("cli:right-flow", include_terminal=True)
                return task is not None and task.state.value == "done" and task.final_result_text == "产物已生成"

            await _wait_for(_task_completed, timeout=2.0)
            task = kernel.latest_task_for_session("cli:right-flow", include_terminal=True)
            assert task is not None
            assert task.delivery_target is not None
            assert task.delivery_target.channel == "cli"
            assert task.delivery_target.chat_id == "direct"
            await _wait_for(lambda: transport.outbound_size >= 2, timeout=2.0)

            outbound_messages = [await transport.consume_outbound() for _ in range(transport.outbound_size)]
            contents = [item.content for item in outbound_messages]
            assert any("开始处理" in item for item in contents)
            assert any("产物已生成" in item or "已完成" in item for item in contents)
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_async_executor_flow() -> None:
    asyncio.run(_exercise_kernel_async_executor_flow())


async def _exercise_kernel_sync_executor_flow() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        [
            _brain_packet(
                final_message="已接收，开始处理。",
                actions={
                    "type": "execute",
                    "task_id": "new",
                    "goal": "整理项目结构",
                    "current_checks": ["帮我整理一下项目结构"],
                },
            ),
            _brain_packet(
                final_message="产物已生成。",
            ),
        ]
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        kernel.executor_runtime._executor = _ExecutorStub()
        try:
            stream_id = await kernel.open_user_stream(
                session_id="cli:sync-right-flow",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_sync_right_flow",
            )
            reply = await kernel.commit_user_stream(
                session_id="cli:sync-right-flow",
                stream_id=stream_id,
                committed_text="帮我整理一下项目结构",
            )

            task = kernel.latest_task_for_session("cli:sync-right-flow", include_terminal=True)
            assert task is not None
            assert task.delivery_target is not None
            assert task.delivery_target.delivery_mode == "stream"
            assert task.delivery_target.channel == "cli"
            assert task.delivery_target.chat_id == "direct"
            assert "开始处理" in reply.content

            def _task_completed() -> bool:
                latest = kernel.latest_task_for_session("cli:sync-right-flow", include_terminal=True)
                return latest is not None and latest.state.value == "done"

            await _wait_for(_task_completed, timeout=2.0)
            await _wait_for(lambda: transport.outbound_size >= 2, timeout=2.0)

            outbound_messages = [await transport.consume_outbound() for _ in range(transport.outbound_size)]
            assert any(item.metadata.get("_stream_state") == "close" for item in outbound_messages)
            assert any(item.metadata.get("task_id") == task.task_id for item in outbound_messages)
            assert any("开始处理" in item.content for item in outbound_messages)
            assert any("产物已生成" in item.content for item in outbound_messages)
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_sync_executor_flow() -> None:
    asyncio.run(_exercise_kernel_sync_executor_flow())


async def _exercise_kernel_forwards_attachments_to_executor() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        [
            _brain_packet(
                final_message="我先看看附件内容。",
                actions={
                    "type": "execute",
                    "task_id": "new",
                    "goal": "结合附件处理请求",
                    "current_checks": ["读取附件并结合用户请求处理"],
                },
            ),
            _brain_packet(
                final_message="附件已经处理好了。",
            ),
        ]
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        executor = _ExecutorStub()
        kernel.executor_runtime._executor = executor
        try:
            await kernel.handle_user_message(
                session_id="cli:attachment-flow",
                channel="cli",
                chat_id="direct",
                sender_id="user",
                message_id="msg_attachment",
                content="请结合附件处理",
                attachments=["/tmp/example.png"],
            )

            def _task_completed() -> bool:
                task = kernel.latest_task_for_session("cli:attachment-flow", include_terminal=True)
                return task is not None and task.state.value == "done"

            await _wait_for(_task_completed, timeout=2.0)
            task = kernel.latest_task_for_session("cli:attachment-flow", include_terminal=True)
            assert task is not None
            assert [block.path for block in task.request.content_blocks] == ["/tmp/example.png"]
            assert executor.last_task_spec is not None
            assert executor.last_task_spec["media"] == ["/tmp/example.png"]
        finally:
            await kernel.stop()


def test_runtime_kernel_forwards_attachments_to_executor() -> None:
    asyncio.run(_exercise_kernel_forwards_attachments_to_executor())


async def _exercise_kernel_drops_late_reply_after_timeout() -> None:
    transport = TransportBus()
    brain_llm = _SlowBrainLLM(
        _brain_packet(
            final_message="这是一条超时后的旧回复。",
        ),
        delay_s=0.05,
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        try:
            try:
                await kernel.handle_user_message(
                    session_id="cli:timeout",
                    channel="cli",
                    chat_id="direct",
                    sender_id="user",
                    message_id="msg_timeout",
                    content="慢一点",
                    timeout_s=0.01,
                )
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError("expected timeout")

            await asyncio.sleep(0.08)
            assert transport.outbound_size == 0
        finally:
            await kernel.stop()


def test_runtime_kernel_drops_late_reply_after_timeout() -> None:
    asyncio.run(_exercise_kernel_drops_late_reply_after_timeout())
