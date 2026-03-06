"""MCP (Model Context Protocol) tool.

MCPTool: MCP 远程工具调用
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from emoticorebot.tools.base import Tool


class MCPTool(Tool):
    """MCP 远程工具调用"""
    
    def __init__(self, session_manager):
        self.session_manager = session_manager
    
    @property
    def name(self) -> str:
        return "mcp_call"
    
    @property
    def description(self) -> str:
        return "Call a tool from MCP server."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server_name": {"type": "string"},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["server_name", "tool_name", "arguments"],
        }
    
    async def execute(self, server_name: str, tool_name: str, arguments: dict, **kwargs: Any) -> str:
        try:
            session = self.session_manager.get_session(server_name)
            if not session:
                return f"Error: MCP server '{server_name}' not found"
            
            result = await session.call_tool(tool_name, arguments)
            
            if result.isError:
                return f"Error: {result.content[0].text if result.content else 'Unknown error'}"
            
            return "\n".join(
                item.text if hasattr(item, "text") else str(item)
                for item in result.content
            )
        
        except Exception as e:
            return f"Error calling MCP tool: {e}"


__all__ = ["MCPTool"]
