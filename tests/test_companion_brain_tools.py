from __future__ import annotations

from emoticorebot.brain.companion_brain import CompanionBrain


def test_build_tools_succeeds_with_langchain_tool_wrapper() -> None:
    brain = CompanionBrain(brain_llm=None, context_builder=None)

    tools = brain._build_tools(
        task_system=None,
        current_context={},
        channel="cli",
        chat_id="direct",
        session_id="cli:direct",
    )

    assert len(tools) == 4
