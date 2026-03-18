from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from emoticorebot.right.deep_agent_backend import build_backend, build_interrupt_on, build_tools, build_task_profile
from emoticorebot.tools import ExecTool, ToolRegistry, WriteFileTool


def _build_service(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(workspace=str(workspace)))


def test_backend_routes_workspace_root_to_real_filesystem() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        backend_factory = build_backend(_build_service(workspace))
        assert backend_factory is not None

        runtime = SimpleNamespace(state={})
        backend = backend_factory(runtime)

        result = backend.write("/sub.py", "def sub(a, b):\n    return a + b")

        assert result.error is None
        assert (workspace / "sub.py").exists()
        assert (workspace / "sub.py").read_text(encoding="utf-8") == "def sub(a, b):\n    return a + b"
        assert runtime.state.get("files", {}) == {}


def test_backend_keeps_state_namespace_ephemeral() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        backend_factory = build_backend(_build_service(workspace))
        assert backend_factory is not None

        runtime = SimpleNamespace(state={})
        backend = backend_factory(runtime)

        result = backend.write("/state/scratch.txt", "temporary note")

        assert result.error is None
        assert not (workspace / "state" / "scratch.txt").exists()
        assert "/scratch.txt" in runtime.state.get("files", {})
        assert "temporary note" in backend.read("/state/scratch.txt")


def test_backend_interrupts_do_not_include_message_tool() -> None:
    interrupt_on = build_interrupt_on()

    assert "message" not in interrupt_on
    assert "cron" in interrupt_on


def test_simple_file_tasks_do_not_expose_exec_tool() -> None:
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        registry = ToolRegistry()
        registry.register(WriteFileTool(workspace=workspace))
        registry.register(ExecTool(working_dir=str(workspace)))
        service = SimpleNamespace(
            context=SimpleNamespace(workspace=str(workspace)),
            assistant_role="worker",
            tools=registry,
            tool_runtime=SimpleNamespace(report_progress=None),
        )

        profile = build_task_profile({"request": "创建一个 add.py 文件，返回 a+b"})
        tools = build_tools(service, profile=profile)
        names = {tool.name for tool in tools}

        assert "write_file" in names
        assert "exec" not in names
