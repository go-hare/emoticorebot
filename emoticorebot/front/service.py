"""User-facing front service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from emoticorebot.front.prompt import FrontPromptBuilder
from emoticorebot.state.schemas import MemoryView
from emoticorebot.utils.llm_utils import extract_message_text


class FrontService:
    """Fast conversational layer that talks to the user first."""

    def __init__(self, workspace: Path, model: Any):
        self.workspace = workspace
        self.model = model
        self.prompts = FrontPromptBuilder(workspace)

    async def reply(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        system_text = (self.workspace / "templates" / "FRONT.md").read_text(encoding="utf-8")
        user_prompt = self.prompts.build_user_prompt(user_text=user_text, memory=memory)
        messages = [SystemMessage(content=system_text), HumanMessage(content=user_prompt)]
        return await self.run(messages, stream_handler)

    async def run(
        self,
        messages: list[SystemMessage | HumanMessage],
        stream_handler: Callable[[str], Awaitable[None]] | None,
    ) -> str:
        if stream_handler is not None and hasattr(self.model, "astream"):
            full_text = ""
            async for chunk in self.model.astream(messages):
                text = extract_message_text(chunk)
                if not text:
                    continue
                full_text += text
                await stream_handler(text)
            return full_text.strip()

        if hasattr(self.model, "ainvoke"):
            response = await self.model.ainvoke(messages)
            return extract_message_text(response)

        if hasattr(self.model, "invoke"):
            response = self.model.invoke(messages)
            return extract_message_text(response)

        raise RuntimeError("Front model does not support invoke or astream")
