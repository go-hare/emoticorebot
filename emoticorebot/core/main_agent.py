"""Main core agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.core.parser import parse_json_model
from emoticorebot.core.schemas import MainDecision
from emoticorebot.state.schemas import MemoryView, UserEvent, WorldState
from emoticorebot.utils.llm_utils import extract_message_text


class CoreMainAgent:
    """Produce state patch, memory patch, checks, and speak intent."""

    def __init__(self, workspace: Path, model: Any):
        self.workspace = workspace
        self.model = model

    async def decide(
        self,
        *,
        trigger: dict[str, Any],
        memory: MemoryView,
        world_state: WorldState,
        front_observation: dict[str, Any],
    ) -> MainDecision:
        system_text = (self.workspace / "templates" / "CORE_MAIN.md").read_text(encoding="utf-8")
        payload = {
            "trigger": trigger,
            "memory": memory.model_dump(),
            "world_state": world_state.model_dump(),
            "front_observation": front_observation,
        }
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ]
        response = await self.run(messages)
        return parse_json_model(response, MainDecision)

    async def run(self, messages: list[SystemMessage | HumanMessage]) -> str:
        if hasattr(self.model, "ainvoke"):
            response = await self.model.ainvoke(messages)
            return extract_message_text(response)
        if hasattr(self.model, "invoke"):
            response = self.model.invoke(messages)
            return extract_message_text(response)
        raise RuntimeError("Core main model does not support invoke")
