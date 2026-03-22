"""Execution tool registry construction."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.config.schema import ToolsConfig
from emoticorebot.tools import (
    DeleteLinesTool,
    EditFileTool,
    ExecTool,
    InsertLinesTool,
    ListDirTool,
    ReadFileTool,
    ReplaceLinesTool,
    SearchFilesTool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)


def build_tool_registry(workspace: Path, tools_config: ToolsConfig) -> ToolRegistry:
    registry = ToolRegistry()
    allowed_dir = workspace if tools_config.restrict_to_workspace else None
    registry.register(ReadFileTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(WriteFileTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(EditFileTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(ListDirTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(SearchFilesTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(InsertLinesTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(DeleteLinesTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(ReplaceLinesTool(workspace=workspace, allowed_dir=allowed_dir))
    registry.register(
        ExecTool(
            working_dir=str(workspace),
            timeout=tools_config.exec.timeout,
            restrict_to_workspace=tools_config.restrict_to_workspace,
            path_append=tools_config.exec.path_append,
        )
    )
    registry.register(WebSearchTool(api_key=tools_config.web.search.api_key))
    registry.register(WebFetchTool())
    return registry
