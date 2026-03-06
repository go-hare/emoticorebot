"""子任务管理器 - 后台并发派生子 agent。

主 agent 通过 SpawnTool 调用 SubagentManager.spawn()，
子任务在独立的 asyncio.Task 中运行，完成后通过 MessageBus 通知主 agent。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from loguru import logger

from emoticorebot.bus.events import InboundMessage
from emoticorebot.bus.queue import MessageBus
from emoticorebot.utils.llm_utils import extract_message_text
from emoticorebot.tools import (
    EditFileTool,
    ExecTool,
    ListDirTool,
    ReadFileTool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)


class SubagentManager:
    """后台子任务管理器。"""

    def __init__(
        self,
        workspace: Path,
        bus: MessageBus,
        iq_llm,  # LangChain BaseChatModel
        eq_llm,
        brave_api_key: str | None = None,
        exec_config=None,
        restrict_to_workspace: bool = False,
    ):
        from emoticorebot.config.schema import ExecToolConfig

        self.workspace = workspace
        self.bus = bus
        self.iq_llm = iq_llm
        self.eq_llm = eq_llm
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._session_tasks: dict[str, set[str]] = {}

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """创建后台子任务。"""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)
        logger.info("✨ Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """执行子任务（后台运行）。"""
        logger.info("🔧 Subagent [{}] starting task: {}", task_id, label)
        try:
            tools = self._build_tools()
            system_prompt = self._build_prompt()

            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=task),
            ]

            llm = self.iq_llm.bind_tools(tools.get_definitions())
            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1
                response = await llm.ainvoke(messages)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    messages.append(
                        AIMessage(
                            content=extract_message_text(response),
                            tool_calls=[
                                {"id": tc["id"], "name": tc["name"], "args": tc.get("args", {})}
                                for tc in response.tool_calls
                            ],
                        )
                    )
                    for tc in response.tool_calls:
                        result = await tools.execute(tc["name"], tc.get("args", {}))
                        messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
                else:
                    final_result = extract_message_text(response)
                    break

            if final_result is None:
                final_result = "Task completed but no final response was generated."

            logger.info("✅ Subagent [{}] completed", task_id)
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            logger.error("❌ Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error")

    def _build_tools(self) -> ToolRegistry:
        """构建子任务专用工具集。"""
        tools = ToolRegistry()
        allowed_dir = self.workspace if self.restrict_to_workspace else None

        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))

        tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            )
        )
        tools.register(WebSearchTool(api_key=self.brave_api_key))
        tools.register(WebFetchTool())
        return tools

    def _build_prompt(self) -> str:
        """构建子任务专用 system prompt。"""
        from datetime import datetime
        import time as _time

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"

        return f"""# Subagent

## Current Time
{now} ({tz})

You are a subagent spawned by the main agent to complete a specific task.

## Rules
1. Stay focused - complete only the assigned task
2. Your final response will be reported back to the main agent
3. Be concise but informative in your findings

## Capabilities
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages

## Workspace
{self.workspace}

When you have completed the task, provide a clear summary of your findings or actions."""

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """通过消息总线通知主 agent 子任务完成。"""
        status_text = "completed" if status == "ok" else "failed"
        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user (1-2 sentences)."""

        msg = InboundMessage(
            channel=origin["channel"],
            sender_id="subagent",
            chat_id=origin["chat_id"],
            content=announce_content,
            metadata={"_subagent_result": True, "task_id": task_id},
        )
        await self.bus.publish_inbound(msg)
        logger.debug(
            "📤 Subagent [{}] result sent to {}:{}", task_id, origin["channel"], origin["chat_id"]
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """取消指定会话的所有子任务。"""
        tasks = [
            self._running_tasks[tid]
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """返回当前运行中的子任务数量。"""
        return len(self._running_tasks)
