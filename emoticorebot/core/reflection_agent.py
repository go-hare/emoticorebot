"""Reflection agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.core.parser import parse_json_model
from emoticorebot.core.schemas import ReflectionResult
from emoticorebot.state.schemas import ReflectionRequest
from emoticorebot.utils.llm_utils import extract_message_text


class CoreReflectionAgent:
    """Produce memory updates from reflection triggers."""

    def __init__(self, workspace: Path, model: Any):
        self.workspace = workspace
        self.model = model

    async def reflect(self, request: ReflectionRequest) -> ReflectionResult:
        system_text = (self.workspace / "templates" / "CORE_REFLECTION.md").read_text(encoding="utf-8")
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=json.dumps(request.model_dump(), ensure_ascii=False, indent=2)),
        ]
        response = await self.run(messages)
        return parse_json_model(response, ReflectionResult)

    async def run(self, messages: list[SystemMessage | HumanMessage]) -> str:
        if hasattr(self.model, "ainvoke"):
            response = await self.model.ainvoke(messages)
            return extract_message_text(response)
        if hasattr(self.model, "invoke"):
            response = self.model.invoke(messages)
            return extract_message_text(response)
        raise RuntimeError("Reflection model does not support invoke")
