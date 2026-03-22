"""Core runtime powered by OpenAI Agents handoffs."""

from __future__ import annotations

import json
from pathlib import Path

from agents import Agent, Runner

from emoticorebot.config.schema import ModelModeConfig, ToolsConfig
from emoticorebot.core.schemas import CoreResult
from emoticorebot.providers.factory import AgentModelBundle
from emoticorebot.sleep.runtime import SleepRuntime
from emoticorebot.state import CurrentStateStore, MemoryStore, SkillStore, WorldModelStore
from emoticorebot.state.schemas import MemoryView, UserEvent
from emoticorebot.tools.agent_tools import AgentToolContext, AgentTools


class CoreRuntime:
    """Single backend brain that can hand off to executor and reflection delegates."""

    def __init__(
        self,
        workspace: Path,
        model_bundle: AgentModelBundle,
        mode: ModelModeConfig,
        memory_store: MemoryStore,
        world_model_store: WorldModelStore,
        current_state_store: CurrentStateStore,
        skill_store: SkillStore,
        tools_config: ToolsConfig,
        sleep_runtime: SleepRuntime,
    ):
        self.workspace = workspace
        self.model_bundle = model_bundle
        self.mode = mode
        self.memory_store = memory_store
        self.world_model_store = world_model_store
        self.current_state_store = current_state_store
        self.skill_store = skill_store
        self.tools_config = tools_config
        self.sleep_runtime = sleep_runtime

    async def run_user_event(self, event: UserEvent, front_reply: str) -> CoreResult:
        memory = self.memory_store.build_memory_view(event.thread_id, event.session_id, event.user_text)
        world_model = self.world_model_store.load()
        skill_context = self.skill_store.render_context(event.user_text, limit=4)
        tool_context = AgentToolContext(
            workspace=self.workspace,
            thread_id=event.thread_id,
            session_id=event.session_id,
            user_id=event.user_id,
            turn_id=event.event_id,
            latest_user_text=event.user_text,
            latest_front_reply=front_reply,
            memory_store=self.memory_store,
            world_model_store=self.world_model_store,
            current_state_store=self.current_state_store,
            skill_store=self.skill_store,
            tools_config=self.tools_config,
        )
        tools = AgentTools(tool_context)
        coordinator = Agent(
            name="core",
            instructions=(self.workspace / "templates" / "CORE_MAIN.md").read_text(encoding="utf-8"),
            tools=tools.build_core_tools(),
            handoffs=[
                self.build_executor_agent(tool_context),
                self.sleep_runtime.build_reflection_agent(tool_context),
                self.sleep_runtime.build_sleep_agent(tool_context),
            ],
            model=self.model_bundle.model,
            model_settings=self.model_bundle.model_settings,
            output_type=CoreResult,
        )
        input_text = self.build_input_text(
            event=event,
            front_reply=front_reply,
            memory=memory,
            world_model=world_model.model_dump(),
            skill_context=skill_context,
        )
        result = await Runner.run(coordinator, input_text, max_turns=self.mode.max_tool_iterations)
        return result.final_output_as(CoreResult, raise_if_incorrect_type=True)

    def build_executor_agent(self, context: AgentToolContext) -> Agent:
        tools = AgentTools(context).build_executor_tools()
        return Agent(
            name="executor",
            handoff_description="当需要真实读取文件、修改文件、执行命令、联网检索或核实项目事实时移交给 executor。",
            instructions=(self.workspace / "templates" / "EXECUTOR.md").read_text(encoding="utf-8"),
            tools=tools,
            model=self.model_bundle.model,
            model_settings=self.model_bundle.model_settings,
            output_type=CoreResult,
        )

    def build_input_text(
        self,
        *,
        event: UserEvent,
        front_reply: str,
        memory: MemoryView,
        world_model: dict[str, object],
        skill_context: str,
    ) -> str:
        sections = [
            "## Event",
            json.dumps(
                {
                    "event_id": event.event_id,
                    "thread_id": event.thread_id,
                    "session_id": event.session_id,
                    "user_id": event.user_id,
                    "user_text": event.user_text,
                    "created_at": event.created_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "## Front Observation",
            f"user_text: {event.user_text}",
            f"front_reply: {front_reply}",
            "",
            "## World Model",
            json.dumps(world_model, ensure_ascii=False, indent=2),
        ]
        recent_dialogue = self.render_recent_rows(memory.raw_layer.get("recent_dialogue", []), role_key="role")
        recent_tools = self.render_recent_rows(memory.raw_layer.get("recent_tools", []), role_key="tool_name")
        cognitive = self.render_cognitive(memory.cognitive_layer)
        long_term = str(memory.long_term_layer.get("summary", "") or "").strip()
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        current_state = str(memory.current_state or "").strip()
        if recent_dialogue:
            sections.extend(["", "## Recent Dialogue", recent_dialogue])
        if recent_tools:
            sections.extend(["", "## Recent Tools", recent_tools])
        if cognitive:
            sections.extend(["", "## Cognitive Memory", cognitive])
        if long_term:
            sections.extend(["", "## Long Term Memory", long_term])
        if user_anchor:
            sections.extend(["", "## User Anchor", user_anchor])
        if soul_anchor:
            sections.extend(["", "## Soul Anchor", soul_anchor])
        if current_state:
            sections.extend(["", "## Current State", current_state])
        if skill_context:
            sections.extend(["", "## Relevant Skills", skill_context])
        return "\n".join(sections).strip()

    def render_recent_rows(self, rows: list[dict[str, object]], role_key: str) -> str:
        rendered: list[str] = []
        for row in rows[-6:]:
            role = str(row.get(role_key, "") or "").strip() or "unknown"
            content = str(row.get("content", "") or "").strip()
            if content:
                rendered.append(f"{role}: {content}")
        return "\n".join(rendered)

    def render_cognitive(self, rows: list[dict[str, object]]) -> str:
        rendered: list[str] = []
        for row in rows[-6:]:
            summary = str(row.get("summary", "") or "").strip()
            outcome = str(row.get("outcome", "") or "").strip()
            if summary and outcome:
                rendered.append(f"- [{outcome}] {summary}")
            elif summary:
                rendered.append(f"- {summary}")
        return "\n".join(rendered)
