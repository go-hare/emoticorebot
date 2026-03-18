from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from emoticorebot.runtime.kernel import RuntimeKernel
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.right.tool_runtime import ExecutionToolRuntime


class _FakeBrainLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[Any] = []

    async def ainvoke(self, prompt: Any):
        self.prompts.append(prompt)
        return self.response


class _FakeContextBuilder:
    def build_brain_decision_system_prompt(self, *, query: str = "") -> str:
        return f"decision system for: {query}"

    def build_brain_system_prompt(self, *, query: str = "") -> str:
        return f"full system for: {query}"


class _RightExecutor:
    def __init__(self) -> None:
        self.tool_runtime = ExecutionToolRuntime()

    async def execute(self, task_spec, *, task_id: str, progress_reporter=None, trace_reporter=None):
        del task_spec, task_id, trace_reporter
        await self.tool_runtime.audit(decision="accept", reason="audit_tool 返回任务可以开始。")
        if progress_reporter is not None:
            await progress_reporter(
                "已完成项目扫描。",
                {
                    "event": "task.tool",
                    "producer": "worker",
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
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="none",
            final_decision="answer",
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


async def _exercise_kernel_async_right_brain_flow() -> None:
    transport = TransportBus()
    brain_llm = _FakeBrainLLM(
        _brain_packet(
            task_action="create_task",
            final_decision="continue",
            final_message="已接收，开始处理。",
            task_reason="需要右脑执行",
            task={
                "title": "整理项目结构",
                "request": "整理项目结构并输出要点",
                "review_policy": "skip",
                "preferred_agent": "worker",
            },
        )
    )
    with TemporaryDirectory() as tmp_dir:
        kernel = RuntimeKernel(
            workspace=Path(tmp_dir),
            transport=transport,
            brain_llm=brain_llm,
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
            await _wait_for(lambda: transport.outbound_size >= 2, timeout=2.0)

            outbound_messages = [await transport.consume_outbound() for _ in range(transport.outbound_size)]
            contents = [item.content for item in outbound_messages]
            assert any("开始处理" in item for item in contents)
            assert any("产物已生成" in item or "已完成" in item for item in contents)
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_async_right_brain_flow() -> None:
    asyncio.run(_exercise_kernel_async_right_brain_flow())
