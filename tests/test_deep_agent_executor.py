from __future__ import annotations

from types import SimpleNamespace

from emoticorebot.execution.backend import GENERAL_TASK_PROFILE, build_agent_instructions, build_task_profile
from emoticorebot.execution.deep_agent_executor import DeepAgentExecutor


def _build_executor() -> DeepAgentExecutor:
    context = SimpleNamespace(workspace="D:/tmp/workspace", build_media_context=lambda media: [])
    return DeepAgentExecutor(worker_llm=None, tool_registry=None, context_builder=context)


def test_worker_waiting_input_is_preserved() -> None:
    executor = _build_executor()

    result = executor._normalize_task_result(
        {
            "control_state": "waiting_input",
            "status": "pending",
            "missing": ["city"],
            "recommended_action": "请提供城市名称",
            "analysis": "",
            "message": "",
        }
    )

    assert result["control_state"] == "waiting_input"
    assert result["status"] == "pending"
    assert result["missing"] == ["city"]
    assert result["recommended_action"] == "请提供城市名称"
    assert "缺少继续执行所需信息" in result["message"]


def test_worker_agent_instructions_allow_waiting_input() -> None:
    service = SimpleNamespace(
        context=SimpleNamespace(workspace="D:/tmp/workspace"),
        assistant_role="worker",
    )

    prompt = build_agent_instructions(service)

    assert "`waiting_input`" in prompt
    assert "缺少关键信息但任务仍可恢复" in prompt
    assert "`failed`" in prompt


def test_worker_executor_instructions_use_worker_identity() -> None:
    prompt = build_agent_instructions(_build_executor())

    assert "你是 `worker`" in prompt


def test_simple_file_task_profile_disables_exec() -> None:
    profile = build_task_profile({"request": "创建一个 add.py 文件，add(a, b) 返回 a + b"})

    assert profile.name == "simple_file"
    assert profile.allow_exec is False
    assert "不要使用 `exec`" in profile.system_hint


def test_general_task_profile_keeps_exec_available() -> None:
    profile = build_task_profile({"request": "创建 add.py 并运行 pytest 验证结果"})

    assert profile == GENERAL_TASK_PROFILE
