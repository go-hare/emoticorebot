from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from emoticorebot.right_brain.backend import build_agent_tools
from emoticorebot.tools import ExecTool, ToolRegistry, WriteFileTool


def test_right_backend_exposes_registered_tools_without_profile_split() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        registry = ToolRegistry()
        registry.register(WriteFileTool(workspace=workspace))
        registry.register(ExecTool(working_dir=str(workspace)))
        service = SimpleNamespace(
            context=SimpleNamespace(workspace=str(workspace)),
            assistant_role="right_brain",
            tools=registry,
            run_hooks=SimpleNamespace(report_progress=None),
        )

        tools = build_agent_tools(service)
        names = {tool.name for tool in tools}

        assert "write_file" in names
        assert "exec" in names
