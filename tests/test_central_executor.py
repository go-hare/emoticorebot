from __future__ import annotations

from types import SimpleNamespace

from emoticorebot.execution.backend import build_agent_instructions
from emoticorebot.execution.central_executor import CentralExecutor


def _build_executor() -> CentralExecutor:
    context = SimpleNamespace(workspace="D:/tmp/workspace", build_media_context=lambda media: [])
    return CentralExecutor(central_llm=None, tool_registry=None, context_builder=context)


def test_central_waiting_input_is_normalized_to_failed() -> None:
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

    assert result["control_state"] == "failed"
    assert result["status"] == "failed"
    assert result["missing"] == ["city"]
    assert result["recommended_action"] == "请提供城市名称"
    assert "缺少继续执行所需信息" in result["message"]


def test_central_agent_instructions_forbid_waiting_input() -> None:
    service = SimpleNamespace(context=SimpleNamespace(workspace="D:/tmp/workspace"))

    prompt = build_agent_instructions(service)

    assert "不要返回 `waiting_input`" in prompt
    assert "如果缺少关键信息无法继续：直接使用 `failed`" in prompt
