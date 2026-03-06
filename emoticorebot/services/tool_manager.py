"""ToolManager - 工具管理服务。

职责（单一）：注册 / 配置 / 连接 / 释放所有工具，向 IQService 提供 ToolRegistry。

工具注册逻辑集中在此，避免分散到 Runtime 或 IQService 内部。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from emoticorebot.tools import (
    CronTool,
    EditFileTool,
    ExecTool,
    ListDirTool,
    MessageTool,
    ReadFileTool,
    SpawnTool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)

if TYPE_CHECKING:
    from emoticorebot.config.schema import ExecToolConfig
    from emoticorebot.cron.service import CronService
    from emoticorebot.bus.queue import MessageBus


class ToolManager:
    """工具管理服务。

    生命周期：
    1. __init__ → 配置参数
    2. register_default_tools() → 注册文件/执行/网络/消息/cron 工具
    3. register_spawn_tool(manager) → 注册 spawn 工具（可选）
    4. set_context(...) → 每次请求前注入 channel/chat_id
    5. connect_mcp_servers(...) → 一次性连接 MCP（可选）
    6. close_mcp() → 关闭连接（应用退出时）
    """

    def __init__(
        self,
        workspace: Path,
        exec_config: "ExecToolConfig",
        bus: "MessageBus | None" = None,
        cron_service: "CronService | None" = None,
        brave_api_key: str | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.workspace = workspace
        self.exec_config = exec_config
        self.bus = bus
        self.cron_service = cron_service
        self.brave_api_key = brave_api_key
        self.restrict_to_workspace = restrict_to_workspace

        self.registry = ToolRegistry()
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False

    def register_default_tools(self) -> None:
        """注册默认工具集（文件 / 执行 / 网络 / 消息 / cron）。"""
        allowed_dir = self.workspace if self.restrict_to_workspace else None

        # 文件操作工具
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.registry.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))

        # Shell 执行工具
        self.registry.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            )
        )

        # 网络工具
        self.registry.register(WebSearchTool(api_key=self.brave_api_key))
        self.registry.register(WebFetchTool())

        # 消息推送工具
        if self.bus:
            self.registry.register(MessageTool(send_callback=self.bus.publish_outbound))

        # 定时任务工具
        if self.cron_service:
            self.registry.register(CronTool(self.cron_service))

        logger.info("Default tools registered: {} tools", len(self.registry.get_definitions()))

    def register_spawn_tool(self, subagent_manager: object) -> None:
        """注册子任务派生工具（依赖 SubagentManager）。"""
        self.registry.register(SpawnTool(subagent_manager))
        logger.debug("Spawn tool registered")

    def set_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """每次请求前注入当前对话上下文（channel / chat_id）。"""
        for name in ("message", "cron", "spawn"):
            if tool := self.registry.get(name):
                if hasattr(tool, "set_context"):
                    if name == "message":
                        tool.set_context(channel, chat_id, message_id)
                    elif name == "spawn" and session_key:
                        tool.set_context(channel, chat_id, session_key)
                    else:
                        tool.set_context(channel, chat_id)

    async def connect_mcp_servers(self, mcp_servers: dict) -> None:
        """连接 MCP 服务器并注册远程工具（仅执行一次）。"""
        if self._mcp_connected or self._mcp_connecting or not mcp_servers:
            return

        self._mcp_connecting = True
        from emoticorebot.core.mcp import connect_mcp_servers

        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(mcp_servers, self.registry, self._mcp_stack)
            self._mcp_connected = True
            logger.info("✅ MCP servers connected successfully")
        except Exception as e:
            logger.error("Failed to connect MCP servers: {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    async def close_mcp(self) -> None:
        """关闭 MCP 连接（应用退出时调用）。"""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._mcp_stack = None
            self._mcp_connected = False
            logger.info("MCP servers closed")

    def get_registry(self) -> ToolRegistry:
        """获取工具注册表（供 IQService 使用）。"""
        return self.registry


__all__ = ["ToolManager"]
