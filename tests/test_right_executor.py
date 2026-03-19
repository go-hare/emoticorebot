from __future__ import annotations

import asyncio
from types import SimpleNamespace

from emoticorebot.right_brain.backend import build_prompt
from emoticorebot.right_brain.executor import RightBrainExecutor


def _build_executor() -> RightBrainExecutor:
    context = SimpleNamespace(workspace="D:/tmp/workspace", build_media_context=lambda media: [])
    return RightBrainExecutor(executor_llm=None, tool_registry=None, context_builder=context)


def test_right_brain_invalid_control_state_is_rejected() -> None:
    executor = _build_executor()

    try:
        executor._normalize_task_result(
            {
                "control_state": "paused",
                "status": "failed",
                "analysis": "",
                "message": "",
            }
        )
    except RuntimeError as exc:
        assert "Invalid task control_state" in str(exc)
    else:
        raise AssertionError("unsupported control_state should be rejected")


def test_right_brain_agent_instructions_require_terminal_audit_on_missing_info() -> None:
    service = SimpleNamespace(
        context=SimpleNamespace(workspace="D:/tmp/workspace"),
        assistant_role="right_brain",
    )

    prompt = build_prompt(service)

    assert 'audit_tool(decision="reject"' in prompt
    assert "等待用户批准、补充或继续" in prompt
    assert "直接调用 `audit_tool(decision=\"reject\", ...)`" in prompt
    assert "缺少关键信息但任务仍可恢复" not in prompt
    assert "`failed`" in prompt
    assert "不要主动枚举一堆技能目录" in prompt
    assert "不支持中途等待用户批准、补充或继续" in prompt
    assert '"recommended_action"' not in prompt
    assert '"confidence"' not in prompt
    assert '"attempt_count"' not in prompt


def test_right_brain_executor_instructions_use_right_brain_identity() -> None:
    prompt = build_prompt(_build_executor())

    assert "你是 `right_brain`" in prompt


def test_right_brain_prompt_uses_single_execution_path() -> None:
    prompt = build_prompt(_build_executor())

    assert "系统已将本次任务标记为简单文件任务" not in prompt
    assert "简单文件任务" not in prompt
    assert "不要用 `exec` 去列目录、读取文件、cat 内容、或做例行验证" in prompt


def test_right_brain_executor_result_keeps_only_minimal_fields() -> None:
    executor = _build_executor()

    result = executor._normalize_task_result(
        {
            "control_state": "completed",
            "status": "success",
            "analysis": "整理完成",
            "message": "产物已生成",
            "missing": ["legacy"],
            "recommended_action": "legacy",
            "confidence": 0.2,
            "attempt_count": 3,
        }
    )

    assert result == {
        "control_state": "completed",
        "status": "success",
        "analysis": "整理完成",
        "message": "产物已生成",
        "task_trace": [],
    }


class _CapturingAgent:
    def __init__(self) -> None:
        self.payload = None

    async def ainvoke(self, payload, *, config=None):
        self.payload = payload
        return {"control_state": "completed", "status": "success", "message": "done"}


async def _exercise_invoke_agent_includes_memory_and_skill_hints() -> None:
    executor = _build_executor()
    agent = _CapturingAgent()

    await executor._invoke_agent(
        agent,
        {
            "request": "处理这个复杂任务",
            "goal": "完成交付",
            "expected_output": "最终结果",
            "memory_refs": ["[workflow_pattern] 复杂任务先收敛再输出"],
            "skill_hints": ["技能 `final-result-execution` | 触发: 多步执行 | 优先最终结果"],
            "task_context": {},
        },
        "thread_1",
        "run_1",
    )

    assert agent.payload is not None
    content = agent.payload["messages"][-1]["content"]
    assert "相关任务经验" in content
    assert "复杂任务先收敛再输出" in content
    assert "技能提示" in content
    assert "final-result-execution" in content


def test_invoke_agent_includes_memory_and_skill_hints() -> None:
    asyncio.run(_exercise_invoke_agent_includes_memory_and_skill_hints())
