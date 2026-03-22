"""OpenAI Agents SDK tool bridge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents import function_tool
from pydantic import BaseModel, Field

from emoticorebot.config.schema import ToolsConfig
from emoticorebot.state import CurrentStateStore, MemoryStore, SkillStore, WorldModelStore
from emoticorebot.state.schemas import CognitiveEvent, LongTermRecord, MemoryCandidate, MemoryPatch, WorldModelUpdate, make_id
from emoticorebot.tools.exec_tool import ExecTool
from emoticorebot.tools.file_tools import EditFileTool, ListDirTool, ReadFileTool, SearchFilesTool, WriteFileTool
from emoticorebot.tools.web_tools import WebFetchTool, WebSearchTool


@dataclass(slots=True)
class AgentToolContext:
    workspace: Path
    thread_id: str
    session_id: str
    user_id: str
    turn_id: str
    latest_user_text: str
    latest_front_reply: str
    memory_store: MemoryStore
    world_model_store: WorldModelStore
    current_state_store: CurrentStateStore
    skill_store: SkillStore
    tools_config: ToolsConfig


class MemoryCandidateInput(BaseModel):
    memory_type: Literal["relationship", "fact", "working", "execution", "reflection"]
    summary: str
    detail: str = ""
    confidence: float = 0.0
    stability: float = 0.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentTools:
    """Build tool sets for coordinator and delegate agents."""

    def __init__(self, context: AgentToolContext):
        self.context = context
        allowed_dir = context.workspace if context.tools_config.restrict_to_workspace else None
        self.read_file_tool = ReadFileTool(workspace=context.workspace, allowed_dir=allowed_dir)
        self.write_file_tool = WriteFileTool(workspace=context.workspace, allowed_dir=allowed_dir)
        self.edit_file_tool = EditFileTool(workspace=context.workspace, allowed_dir=allowed_dir)
        self.list_dir_tool = ListDirTool(workspace=context.workspace, allowed_dir=allowed_dir)
        self.search_files_tool = SearchFilesTool(workspace=context.workspace, allowed_dir=allowed_dir)
        self.exec_tool = ExecTool(
            working_dir=str(context.workspace),
            timeout=context.tools_config.exec.timeout,
            restrict_to_workspace=context.tools_config.restrict_to_workspace,
            path_append=context.tools_config.exec.path_append,
        )
        self.web_search_tool = WebSearchTool(api_key=context.tools_config.web.search.api_key)
        self.web_fetch_tool = WebFetchTool()

    def build_core_tools(self) -> list[Any]:
        @function_tool
        async def read_world_model() -> str:
            model = self.context.world_model_store.load()
            result = json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
            self.log_tool_result("read_world_model", {}, result, True)
            return result

        @function_tool
        async def update_world_model(
            focus: str | None = None,
            mode: Literal["chat", "acting", "waiting"] | None = None,
            recent_intent: str | None = None,
            open_threads: list[str] | None = None,
            last_tool_result: str | None = None,
        ) -> str:
            update = WorldModelUpdate(
                focus=focus,
                mode=mode,
                recent_intent=recent_intent,
                open_threads=open_threads,
                last_tool_result=last_tool_result,
            )
            model = self.context.world_model_store.update(update)
            result = json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
            self.log_tool_result("update_world_model", update.model_dump(exclude_none=True), result, True)
            return result

        @function_tool
        async def read_current_state() -> str:
            result = self.context.current_state_store.read()
            self.log_tool_result("read_current_state", {}, result, True)
            return result

        @function_tool
        async def write_current_state(content: str) -> str:
            self.context.current_state_store.write(content)
            result = "current_state.md updated"
            self.log_tool_result("write_current_state", {"content": content}, result, True)
            return result

        @function_tool(strict_mode=False)
        async def write_cognitive_memory(
            summary: str,
            outcome: str,
            reason: str,
            needs_deep_reflection: bool = False,
            metadata: dict[str, Any] | None = None,
        ) -> str:
            event = CognitiveEvent(
                event_id=make_id("cog"),
                user_id=self.context.user_id,
                session_id=self.context.session_id,
                thread_id=self.context.thread_id,
                turn_id=self.context.turn_id,
                summary=summary,
                outcome=outcome,
                reason=reason,
                needs_deep_reflection=needs_deep_reflection,
                user_text=self.context.latest_user_text,
                assistant_text=self.context.latest_front_reply,
                source_event_ids=[self.context.turn_id],
                metadata=dict(metadata or {}),
            )
            self.context.memory_store.append_patch(MemoryPatch(cognitive_append=[event]))
            result = f"cognitive event saved: {summary}"
            self.log_tool_result("write_cognitive_memory", event.model_dump(), result, True)
            return result

        return [
            read_world_model,
            update_world_model,
            read_current_state,
            write_current_state,
            write_cognitive_memory,
        ]

    def build_executor_tools(self) -> list[Any]:
        @function_tool
        async def read_file(file_path: str, start_line: int | None = None, end_line: int | None = None) -> str:
            return await self.run_tool(
                name="read_file",
                params={"file_path": file_path, "start_line": start_line, "end_line": end_line},
                callback=self.read_file_tool.execute,
            )

        @function_tool
        async def write_file(file_path: str, content: str) -> str:
            return await self.run_tool(
                name="write_file",
                params={"file_path": file_path, "content": content},
                callback=self.write_file_tool.execute,
            )

        @function_tool
        async def edit_file(file_path: str, old_string: str, new_string: str) -> str:
            return await self.run_tool(
                name="edit_file",
                params={"file_path": file_path, "old_string": old_string, "new_string": new_string},
                callback=self.edit_file_tool.execute,
            )

        @function_tool
        async def list_dir(dir_path: str = "") -> str:
            return await self.run_tool(
                name="list_dir",
                params={"dir_path": dir_path},
                callback=self.list_dir_tool.execute,
            )

        @function_tool
        async def search_files(pattern: str, file_pattern: str = "*") -> str:
            return await self.run_tool(
                name="search_files",
                params={"pattern": pattern, "file_pattern": file_pattern},
                callback=self.search_files_tool.execute,
            )

        @function_tool
        async def exec(command: str, working_dir: str = "") -> str:
            return await self.run_tool(
                name="exec",
                params={"command": command, "working_dir": working_dir},
                callback=self.exec_tool.execute,
            )

        @function_tool
        async def web_search(query: str, count: int = 5) -> str:
            return await self.run_tool(
                name="web_search",
                params={"query": query, "count": count},
                callback=self.web_search_tool.execute,
            )

        @function_tool
        async def web_fetch(url: str) -> str:
            return await self.run_tool(
                name="web_fetch",
                params={"url": url},
                callback=self.web_fetch_tool.execute,
            )

        @function_tool
        async def read_world_model() -> str:
            model = self.context.world_model_store.load()
            result = json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
            self.log_tool_result("read_world_model", {}, result, True)
            return result

        @function_tool
        async def update_world_model(
            focus: str | None = None,
            mode: Literal["chat", "acting", "waiting"] | None = None,
            recent_intent: str | None = None,
            open_threads: list[str] | None = None,
            last_tool_result: str | None = None,
        ) -> str:
            update = WorldModelUpdate(
                focus=focus,
                mode=mode,
                recent_intent=recent_intent,
                open_threads=open_threads,
                last_tool_result=last_tool_result,
            )
            model = self.context.world_model_store.update(update)
            result = json.dumps(model.model_dump(), ensure_ascii=False, indent=2)
            self.log_tool_result("update_world_model", update.model_dump(exclude_none=True), result, True)
            return result

        @function_tool
        async def read_current_state() -> str:
            result = self.context.current_state_store.read()
            self.log_tool_result("read_current_state", {}, result, True)
            return result

        @function_tool
        async def write_current_state(content: str) -> str:
            self.context.current_state_store.write(content)
            result = "current_state.md updated"
            self.log_tool_result("write_current_state", {"content": content}, result, True)
            return result

        @function_tool(strict_mode=False)
        async def write_cognitive_memory(
            summary: str,
            outcome: str,
            reason: str,
            needs_deep_reflection: bool = False,
            metadata: dict[str, Any] | None = None,
        ) -> str:
            event = CognitiveEvent(
                event_id=make_id("cog"),
                user_id=self.context.user_id,
                session_id=self.context.session_id,
                thread_id=self.context.thread_id,
                turn_id=self.context.turn_id,
                summary=summary,
                outcome=outcome,
                reason=reason,
                needs_deep_reflection=needs_deep_reflection,
                user_text=self.context.latest_user_text,
                assistant_text=self.context.latest_front_reply,
                source_event_ids=[self.context.turn_id],
                metadata=dict(metadata or {}),
            )
            self.context.memory_store.append_patch(MemoryPatch(cognitive_append=[event]))
            result = f"cognitive event saved: {summary}"
            self.log_tool_result("write_cognitive_memory", event.model_dump(), result, True)
            return result

        return [
            read_file,
            write_file,
            edit_file,
            list_dir,
            search_files,
            exec,
            web_search,
            web_fetch,
            read_world_model,
            update_world_model,
            read_current_state,
            write_current_state,
            write_cognitive_memory,
        ]

    def build_reflection_tools(self) -> list[Any]:
        @function_tool(strict_mode=False)
        async def write_cognitive_memory(
            summary: str,
            outcome: str,
            reason: str,
            needs_deep_reflection: bool = False,
            metadata: dict[str, Any] | None = None,
        ) -> str:
            event = CognitiveEvent(
                event_id=make_id("cog"),
                user_id=self.context.user_id,
                session_id=self.context.session_id,
                thread_id=self.context.thread_id,
                turn_id=self.context.turn_id,
                summary=summary,
                outcome=outcome,
                reason=reason,
                needs_deep_reflection=needs_deep_reflection,
                user_text=self.context.latest_user_text,
                assistant_text=self.context.latest_front_reply,
                source_event_ids=[self.context.turn_id],
                metadata=dict(metadata or {}),
            )
            self.context.memory_store.append_patch(MemoryPatch(cognitive_append=[event]))
            result = f"cognitive event saved: {summary}"
            self.log_tool_result("write_cognitive_memory", event.model_dump(), result, True)
            return result

        @function_tool(strict_mode=False)
        async def write_long_term_memory(
            summary: str,
            memory_candidates: list[MemoryCandidateInput],
            user_updates: list[str] | None = None,
            soul_updates: list[str] | None = None,
        ) -> str:
            record = LongTermRecord(
                record_id=make_id("mem"),
                user_id=self.context.user_id,
                session_id=self.context.session_id,
                thread_id=self.context.thread_id,
                turn_id=self.context.turn_id,
                summary=summary,
                memory_candidates=[
                    MemoryCandidate(
                        memory_id=make_id("cand"),
                        memory_type=item.memory_type,
                        summary=item.summary,
                        detail=item.detail,
                        confidence=item.confidence,
                        stability=item.stability,
                        tags=item.tags,
                        metadata=item.metadata,
                    )
                    for item in memory_candidates
                ],
                user_updates=list(user_updates or []),
                soul_updates=list(soul_updates or []),
                source_event_ids=[self.context.turn_id],
            )
            self.context.memory_store.append_patch(MemoryPatch(long_term_append=[record]))
            result = f"long term memory saved: {summary}"
            self.log_tool_result("write_long_term_memory", record.model_dump(), result, True)
            return result

        @function_tool
        async def append_user_updates(rows: list[str]) -> str:
            patch = MemoryPatch(user_updates=list(rows or []))
            self.context.memory_store.append_patch(patch)
            result = f"user updates saved: {len(rows or [])}"
            self.log_tool_result("append_user_updates", {"rows": rows}, result, True)
            return result

        @function_tool
        async def append_soul_updates(rows: list[str]) -> str:
            patch = MemoryPatch(soul_updates=list(rows or []))
            self.context.memory_store.append_patch(patch)
            result = f"soul updates saved: {len(rows or [])}"
            self.log_tool_result("append_soul_updates", {"rows": rows}, result, True)
            return result

        @function_tool
        async def write_current_state(content: str) -> str:
            self.context.current_state_store.write(content)
            result = "current_state.md updated"
            self.log_tool_result("write_current_state", {"content": content}, result, True)
            return result

        @function_tool
        async def write_skill(slug: str, title: str, description: str, content: str) -> str:
            path = self.context.skill_store.write_generated_skill(
                slug=slug,
                title=title,
                description=description,
                content=content,
                metadata={
                    "source": "sleep",
                    "thread_id": self.context.thread_id,
                    "session_id": self.context.session_id,
                    "turn_id": self.context.turn_id,
                },
            )
            result = f"skill saved: {path}"
            self.log_tool_result("write_skill", {"slug": slug, "title": title}, result, True)
            return result

        return [
            write_cognitive_memory,
            write_long_term_memory,
            append_user_updates,
            append_soul_updates,
            write_current_state,
            write_skill,
        ]

    def build_sleep_tools(self) -> list[Any]:
        return self.build_reflection_tools()

    async def run_tool(self, name: str, params: dict[str, Any], callback: Any) -> str:
        clean_params = {key: value for key, value in params.items() if value is not None}
        try:
            result = await callback(**clean_params)
            success = not str(result).startswith("Error")
            self.log_tool_result(name, clean_params, result, success)
            return result
        except Exception as exc:
            result = f"Error executing {name}: {exc}"
            self.log_tool_result(name, clean_params, result, False)
            return result

    def log_tool_result(self, name: str, params: dict[str, Any], result: str, success: bool) -> None:
        clipped = self.clip_text(result, limit=400)
        self.context.memory_store.append_tool_record(
            self.context.thread_id,
            {
                "role": "tool",
                "tool_name": name,
                "params": params,
                "success": success,
                "content": clipped,
                "turn_id": self.context.turn_id,
                "session_id": self.context.session_id,
                "user_id": self.context.user_id,
                "event_type": "tool_result",
            },
        )
        self.context.world_model_store.update(WorldModelUpdate(last_tool_result=clipped))

    def clip_text(self, text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit] + f"... [truncated {len(value) - limit} chars]"
