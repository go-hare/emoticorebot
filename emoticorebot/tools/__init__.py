"""Tools package - 所有工具的统一出口。

子模块：
- base.py        Tool 抽象基类 + ToolRegistry
- exec_tool.py   ExecTool（Shell 命令执行）
- file_tools.py  文件操作工具（read/write/edit/list/search/insert/delete/replace）
- web_tools.py   网络工具（搜索/抓取）
- system_tools.py 系统工具（消息发送/定时任务）
- mcp_tool.py    MCP 工具封装
"""

from __future__ import annotations

from emoticorebot.tools.base import Tool, ToolRegistry
from emoticorebot.tools.exec_tool import ExecTool
from emoticorebot.tools.file_tools import (
    DeleteLinesTool,
    EditFileTool,
    InsertLinesTool,
    ListDirTool,
    ReadFileTool,
    ReplaceLinesTool,
    SearchFilesTool,
    WriteFileTool,
)
from emoticorebot.tools.mcp_tool import MCPTool
from emoticorebot.tools.system_tools import CronTool, MessageTool
from emoticorebot.tools.web_tools import WebFetchTool, WebSearchTool

__all__ = [
    # 基础
    "Tool",
    "ToolRegistry",
    # 执行
    "ExecTool",
    # 文件
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirTool",
    "SearchFilesTool",
    "InsertLinesTool",
    "DeleteLinesTool",
    "ReplaceLinesTool",
    # 网络
    "WebSearchTool",
    "WebFetchTool",
    # 系统
    "MessageTool",
    "CronTool",
    # MCP
    "MCPTool",
]
