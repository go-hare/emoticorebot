from __future__ import annotations

import asyncio
from pathlib import Path

from emoticorebot.tools.exec_tool import ExecTool


def test_exec_tool_resolves_relative_working_dir_from_workspace(tmp_path) -> None:
    workspace = Path(tmp_path)
    tool = ExecTool(working_dir=str(workspace))

    result = asyncio.run(tool.execute("pwd", working_dir="."))

    assert "Exit code: 0" in result
    assert str(workspace.resolve()) in result


def test_exec_tool_resolves_nested_relative_working_dir_from_workspace(tmp_path) -> None:
    workspace = Path(tmp_path)
    nested = workspace / "nested"
    nested.mkdir()
    tool = ExecTool(working_dir=str(workspace))

    result = asyncio.run(tool.execute("pwd", working_dir="nested"))

    assert "Exit code: 0" in result
    assert str(nested.resolve()) in result
