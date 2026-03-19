from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from emoticorebot.runtime.kernel import RuntimeKernel
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.right_brain.hooks import RunHooks


class _FakeLeftBrainLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any):
        self.prompts.append(prompt)
        return self.response


class _FakeContextBuilder:
    def build_left_brain_decision_system_prompt(self, *, query: str = "") -> str:
        return f"decision system for: {query}"

    def build_left_brain_system_prompt(self, *, query: str = "") -> str:
        return f"full system for: {query}"


class _RightExecutor:
    def __init__(self) -> None:
        self.run_hooks = RunHooks()
        self.last_task_spec: dict[str, Any] | None = None

    async def execute(self, task_spec, *, task_id: str, progress_reporter=None, trace_reporter=None):
        self.last_task_spec = dict(task_spec)
        del task_id, trace_reporter
        await self.run_hooks.audit(decision="accept", reason="audit_tool 返回任务可以开始。")
        if progress_reporter is not None:
            await progress_reporter(
                "已完成项目扫描。",
                {
                    "event": "task.tool",
                    "producer": "right_brain",
                    "phase": "tool",
                    "tool_name": "read_file",
                    "payload": {"progress": 0.5, "next_step": "整理输出"},
                },
            )
        return {
            "control_state": "completed",
            "status": "success",
            "analysis": "整理完成",
            "message": "产物已生成",
            "task_trace": [],
        }


def _left_brain_packet(
    *,
    task_action: str = "none",
    task_mode: str = "skip",
    final_message: str,
    task_reason: str = "",
    task_id: str | None = None,
) -> str:
    lines = [
        "####user####",
        final_message,
        "",
        "####task####",
        f"action={task_action}",
        f"task_mode={task_mode}",
    ]
    if task_reason:
        lines.append(f"reason={task_reason}")
    if task_id:
        lines.append(f"task_id={task_id}")
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
    left_brain_llm = _FakeLeftBrainLLM(
        _left_brain_packet(
            task_action="none",
            task_mode="skip",
            final_message="你好，我在。",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            left_brain_llm=left_brain_llm,
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


async def _exercise_kernel_async_right_brain_flow() -> None:
    transport = TransportBus()
    left_brain_llm = _FakeLeftBrainLLM(
        _left_brain_packet(
            task_action="create_task",
            task_mode="async",
            final_message="已接收，开始处理。",
            task_reason="需要右脑执行",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            left_brain_llm=left_brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        kernel.right_brain_runtime._executor = _RightExecutor()
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


def test_runtime_kernel_runs_async_right_brain_flow() -> None:
    asyncio.run(_exercise_kernel_async_right_brain_flow())


async def _exercise_kernel_sync_right_brain_flow() -> None:
    transport = TransportBus()
    left_brain_llm = _FakeLeftBrainLLM(
        _left_brain_packet(
            task_action="create_task",
            task_mode="sync",
            final_message="已接收，开始处理。",
            task_reason="需要右脑执行",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            left_brain_llm=left_brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        kernel.right_brain_runtime._executor = _RightExecutor()
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
            contents = [item.content for item in outbound_messages]
            assert any("开始处理" in item for item in contents)
            assert any("产物已生成" in item or "已完成" in item for item in contents)
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_sync_right_brain_flow() -> None:
    asyncio.run(_exercise_kernel_sync_right_brain_flow())


async def _exercise_kernel_forwards_attachments_to_right_brain() -> None:
    transport = TransportBus()
    left_brain_llm = _FakeLeftBrainLLM(
        _left_brain_packet(
            task_action="create_task",
            task_mode="async",
            final_message="我先看看附件内容。",
            task_reason="需要读取附件",
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            left_brain_llm=left_brain_llm,
            context_builder=_FakeContextBuilder(),
        )
        executor = _RightExecutor()
        kernel.right_brain_runtime._executor = executor
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


def test_runtime_kernel_forwards_attachments_to_right_brain() -> None:
    asyncio.run(_exercise_kernel_forwards_attachments_to_right_brain())


