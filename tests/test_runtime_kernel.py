from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.runtime.kernel import RuntimeKernel


class _FakeBrainLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        return self.response


def _brain_packet(
    *,
    task_action: str = "none",
    final_decision: str = "answer",
    final_message: str,
    task_reason: str = "",
    intent: str = "",
    working_hypothesis: str = "",
    task_brief: str = "",
    task: dict[str, object] | None = None,
    execution_summary: str = "",
) -> str:
    return json.dumps(
        {
            "task_action": task_action,
            "task_reason": task_reason,
            "final_decision": final_decision,
            "final_message": final_message,
            "task_brief": task_brief,
            "task": task or {},
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "execution_summary": execution_summary,
        },
        ensure_ascii=False,
    )


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
            intent="modify_code",
            working_hypothesis="用户要修改 add.py",
            task_brief="修改 add.py，让 add(a, b) 返回 a + b",
            task={
                "title": "修改 add.py",
                "request": "修改 add.py，让 add(a, b) 返回 a + b",
                "review_policy": "skip",
                "preferred_agent": "worker",
            },
            execution_summary="brain created task",
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

            await _wait_for(lambda: transport.outbound_size >= 2)
            first = await transport.consume_outbound()
            second = await transport.consume_outbound()

            assert "开始处理" in first.content
            assert "已完成" in second.content
            latest_task = kernel.latest_task_for_session("cli:direct", include_terminal=True)
            assert latest_task is not None
            assert latest_task.status.value in {"done", "archived", "assigned", "running"}
        finally:
            await kernel.stop()


def test_runtime_kernel_runs_task_flow() -> None:
    asyncio.run(_exercise_kernel_task_flow())


async def _exercise_kernel_persona_rollback() -> None:
    transport = TransportBus()
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        kernel = RuntimeKernel(workspace=workspace, transport=transport)
        try:
            kernel._memory._persona.apply_updates_result("persona", ["先判断再执行"], scope="deep")
            kernel._memory._persona.apply_updates_result("persona", ["结论优先返回"], scope="deep")

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
            intent="math",
            working_hypothesis="用户在问简单算术",
            execution_summary="brain answered directly",
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
