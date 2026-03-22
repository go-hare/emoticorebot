"""Builders for backend reflection and sleep delegates."""

from __future__ import annotations

from pathlib import Path

from agents import Agent

from emoticorebot.core.schemas import CoreResult
from emoticorebot.providers.factory import AgentModelBundle
from emoticorebot.tools.agent_tools import AgentToolContext, AgentTools


class SleepRuntime:
    """Create reflection-style delegate agents for the core runtime."""

    def __init__(
        self,
        workspace: Path,
        model_bundle: AgentModelBundle,
    ):
        self.workspace = workspace
        self.model_bundle = model_bundle

    def build_reflection_agent(self, context: AgentToolContext) -> Agent:
        tools = AgentTools(context).build_reflection_tools()
        return Agent(
            name="reflection",
            handoff_description="当需要写入浅反思、深反思、长期记忆、用户画像、SOUL 或 skill 时移交给 reflection。",
            instructions=(self.workspace / "templates" / "REFLECTION.md").read_text(encoding="utf-8"),
            tools=tools,
            model=self.model_bundle.model,
            model_settings=self.model_bundle.model_settings,
            output_type=CoreResult,
        )

    def build_sleep_agent(self, context: AgentToolContext) -> Agent:
        tools = AgentTools(context).build_sleep_tools()
        return Agent(
            name="sleep",
            handoff_description="当需要后台整理、沉淀稳定经验、低频整合记忆时移交给 sleep。",
            instructions=(self.workspace / "templates" / "SLEEP.md").read_text(encoding="utf-8"),
            tools=tools,
            model=self.model_bundle.model,
            model_settings=self.model_bundle.model_settings,
            output_type=CoreResult,
        )
