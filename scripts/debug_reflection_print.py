from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.agent.model import LLMFactory
from emoticorebot.agent.reflection import MemoryService, ReflectionCoordinator
from emoticorebot.config.loader import load_config
from emoticorebot.models.emotion_state import EmotionStateManager


async def main() -> None:
    workspace = Path(r"C:\Users\Administrator\.emoticorebot\workspace")
    config = load_config()
    factory = LLMFactory(
        providers_config=config.providers,
        worker_mode=config.agents.defaults.worker_mode,
        brain_mode=config.agents.defaults.brain_mode,
    )
    brain_llm = factory.get_brain()
    emotion_mgr = EmotionStateManager(workspace)
    memory_service = MemoryService(
        workspace,
        memory_config=config.memory,
        providers_config=config.providers,
    )
    coordinator = ReflectionCoordinator(
        workspace,
        emotion_mgr,
        memory_service,
        reflection_llm=brain_llm,
        memory_config=config.memory,
        providers_config=config.providers,
    )

    cognitive_path = CognitiveEvent.storage_path(workspace)
    print("WORKSPACE:", workspace)
    print("COGNITIVE_PATH:", cognitive_path)
    print("COGNITIVE_EXISTS_BEFORE:", cognitive_path.exists())

    original_reflect_turn = coordinator.turn_reflection.reflect_turn

    async def wrapped_reflect_turn(**kwargs):
        print("REFLECT_TURN_INPUT:")
        print(json.dumps(kwargs, ensure_ascii=False, indent=2, default=str))
        result = await original_reflect_turn(**kwargs)
        print("REFLECT_TURN_OUTPUT:")
        print(json.dumps(result.turn_reflection, ensure_ascii=False, indent=2, default=str))
        return result

    coordinator.turn_reflection.reflect_turn = wrapped_reflect_turn  # type: ignore[method-assign]

    reflection_input = {
        "message_id": "debug_reflection_print",
        "session_id": "cli:direct",
        "source_type": "user_turn",
        "user_input": "你好",
        "assistant_output": "你好呀。怎么，终于想起我了？哼……不过还是欢迎你。今天想聊点什么？",
        "channel": "cli",
        "chat_id": "direct",
        "brain": {
            "intent": "greet_user",
            "working_hypothesis": "用户是在开启一段普通对话，期待自然友好的回应。",
            "task_action": "none",
            "task_reason": "只是简单问候，不需要创建任务或追踪执行。",
            "final_decision": "answer",
            "task_brief": "",
            "execution_summary": "直接回应了用户的问候。",
        },
        "metadata": {
            "message_id": "debug_reflection_print",
            "channel": "cli",
            "chat_id": "direct",
            "execution": {
                "summary": "直接回应了用户的问候。",
                "brain_decision": "answer",
                "task_action": "none",
            },
        },
    }

    try:
        result = await coordinator.write_turn_reflection(reflection_input)
        print("WRITE_RESULT:")
        print(result)
    except Exception:
        print("WRITE_EXCEPTION:")
        traceback.print_exc()

    print("COGNITIVE_EXISTS_AFTER:", cognitive_path.exists())
    if cognitive_path.exists():
        lines = cognitive_path.read_text(encoding="utf-8").splitlines()
        print("COGNITIVE_LINE_COUNT:", len(lines))
        if lines:
            print("COGNITIVE_LAST_LINE:")
            print(lines[-1])


if __name__ == "__main__":
    asyncio.run(main())
