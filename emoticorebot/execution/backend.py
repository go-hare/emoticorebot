"""Deep Agent backend wiring for the execution layer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.structured_output import ToolStrategy

from emoticorebot.execution.skills import BUILTIN_SKILLS_DIR
from emoticorebot.protocol.task_result import TaskExecutionResult
from emoticorebot.utils.helpers import ensure_dir

try:
    from emoticorebot.checkpointing import PersistentMemorySaver
except Exception:
    PersistentMemorySaver = None

try:
    from deepagents import create_deep_agent
except Exception:
    create_deep_agent = None

try:
    from langgraph.checkpoint.memory import InMemorySaver
except Exception:
    InMemorySaver = None

if TYPE_CHECKING:
    from emoticorebot.execution.central_executor import CentralExecutor


def deep_agents_available() -> bool:
    return create_deep_agent is not None


def ensure_agent(service: "CentralExecutor") -> Any:
    if service._agent is None:
        service._agent = build_agent(service)
    return service._agent


def build_agent(service: "CentralExecutor") -> Any:
    if create_deep_agent is None:
        raise RuntimeError("deepagents is not available")

    tools = build_tools(service)
    backend = build_backend(service)
    use_virtual_skill_paths = backend is not None
    skills = build_skill_paths(service, virtual_mode=use_virtual_skill_paths)
    checkpointer = ensure_checkpointer(service)
    interrupt_on = build_interrupt_on()

    try:
        kwargs: dict[str, Any] = {
            "model": service.central_llm,
            "tools": tools,
            "system_prompt": build_agent_instructions(service),
            "response_format": ToolStrategy(TaskExecutionResult),
        }
        if skills:
            kwargs["skills"] = skills
        if backend is not None:
            kwargs["backend"] = backend
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer
        if interrupt_on:
            kwargs["interrupt_on"] = interrupt_on
        return create_deep_agent(**kwargs)
    except TypeError as exc:
        raise RuntimeError(f"Deep Agents API mismatch: {exc}") from exc


def ensure_checkpointer(service: "CentralExecutor") -> Any | None:
    if service._checkpointer is not None:
        return service._checkpointer
    workspace = Path(service.context.workspace).expanduser().resolve()
    checkpoint_dir = ensure_dir(workspace / "sessions" / "_checkpoints")
    checkpoint_file = checkpoint_dir / "central.pkl"
    if PersistentMemorySaver is not None:
        service._checkpointer = PersistentMemorySaver(checkpoint_file)
        return service._checkpointer
    if InMemorySaver is None:
        return None
    service._checkpointer = InMemorySaver()
    return service._checkpointer


def build_interrupt_on() -> dict[str, Any]:
    return {
        "cron": {
            "allowed_decisions": ["approve", "reject"],
            "description": "请确认是否允许 central 创建或修改定时任务。",
        },
    }


def build_agent_instructions(service: "CentralExecutor") -> str:
    workspace = Path(service.context.workspace).expanduser().resolve()
    return (
        "你是 `central`，负责复杂问题的规划、执行与结果收口。\n"
        f"当前工作区目录是 `{workspace}`。\n\n"
        "默认文件路径（例如 `/foo.py`）对应当前工作区中的真实文件。\n"
        "只有 `/state/` 前缀用于会话内临时文件；不要把最终交付物写到 `/state/`。\n\n"
        "## 职责\n"
        "1. 接收委托的问题并执行。\n"
        "2. 必要时调用工具，在单次执行内完成任务。\n"
        "3. 给出清晰结论。\n\n"
        "## 边界\n"
        "1. 不负责最终对用户表达，只返回执行结果。\n"
        "2. 不负责人格维护、情绪陪伴。\n"
        "3. 不要输出原始日志、工具轨迹或 JSON 代码块。\n\n"
        "## Task 结构化输出要求\n"
        "系统会强制你输出 `TaskExecutionResult` 结构，不要在 `message` 或 `analysis` 中嵌 JSON。\n"
        "- `control_state` 对你来说只能使用 `completed` 或 `failed`；不要返回 `waiting_input`。\n"
        "- `status` 只能是 `success`、`partial`、`failed`。\n"
        "- 已完成任务：使用 `completed`，并在 `message` 中写最终可交付结果。\n"
        "- 如果缺少关键信息无法继续：直接使用 `failed`，在 `message` 或 `analysis` 中写清楚缺什么、为什么无法继续；如有必要可在 `recommended_action` 中写建议补充项。\n"
        "- 无法继续执行：使用 `failed`，在 `message` 或 `analysis` 中写明失败原因。\n"
        "- `analysis` 只写紧凑结论，不展开冗长推理。\n"
        "- `pending_review` 只有确实需要审核时才填写。\n"
        "- `task_trace` 由系统补充，你不要自己展开执行轨迹。\n"
    )


def build_backend(service: "CentralExecutor") -> Any | None:
    workspace = Path(service.context.workspace).expanduser().resolve()
    workspace_skills_root = (workspace / "skills").resolve()
    builtin_skills_root = BUILTIN_SKILLS_DIR.resolve()

    try:
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
    except Exception:
        return None

    def build_agent_backend(rt: Any) -> Any:
        routes: dict[str, Any] = {
            # Route normal absolute paths like `/foo.py` to the real workspace.
            # Keep `/state/` as the explicit ephemeral namespace backed by runtime state.
            "/": FilesystemBackend(root_dir=workspace, virtual_mode=True),
            "/state/": StateBackend(rt),
        }
        if builtin_skills_root.exists():
            routes["/skills/builtin/"] = FilesystemBackend(
                root_dir=builtin_skills_root,
                virtual_mode=True,
            )
        if workspace_skills_root.exists():
            routes["/skills/workspace/"] = FilesystemBackend(
                root_dir=workspace_skills_root,
                virtual_mode=True,
            )
        return CompositeBackend(
            default=StateBackend(rt),
            routes=routes,
        )

    return build_agent_backend


def build_tools(service: "CentralExecutor") -> list[Any]:
    tools: list[Any] = []
    stage_tool = build_stage_report_tool(service)
    if stage_tool is not None:
        tools.append(stage_tool)
    if service.tools is not None:
        tool_names = [name for name in service.tools.tool_names if name != "message"]
        tools.extend(build_registry_tools(service, tool_names))
    return tools


def build_stage_report_tool(service: "CentralExecutor") -> Any | None:
    """构建阶段性汇报工具，让 agent 可以主动汇报进展。"""
    try:
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field
    except Exception:
        return None

    class StageReportArgs(BaseModel):
        summary: str = Field(description="阶段性进展摘要，简要说明当前完成了什么")
        progress: float = Field(default=0.5, ge=0.0, le=1.0, description="完成度 0-1")
        next_step: str = Field(default="", description="下一步计划，可选")

    async def report_stage(summary: str, progress: float = 0.5, next_step: str = "") -> str:
        return await service.tool_runtime.report_progress(
            str(summary or "").strip(),
            event="task.stage",
            producer="central",
            phase="stage",
            payload={
                "progress": max(0.0, min(1.0, float(progress))),
                "next_step": str(next_step or "").strip(),
            },
        )

    return StructuredTool.from_function(
        coroutine=report_stage,
        name="report_stage",
        description=(
            "向主脑汇报当前阶段性进展。当你完成了一个重要步骤、发现重要信息、"
            "或即将开始耗时操作时使用。不要频繁调用，只在关键节点汇报。"
        ),
        args_schema=StageReportArgs,
    )


def build_registry_tools(service: "CentralExecutor", names: list[str]) -> list[Any]:
    if service.tools is None:
        return []

    built: list[Any] = []
    for name in names:
        tool = build_registry_tool(service, name)
        if tool is not None:
            built.append(tool)
    return built


def build_registry_tool(service: "CentralExecutor", name: str) -> Any | None:
    if service.tools is None:
        return None

    registry_tool = service.tools.get(name) if hasattr(service.tools, "get") else None
    if registry_tool is None:
        return None

    try:
        from langchain_core.tools import StructuredTool
        from pydantic import create_model
    except Exception:
        return None

    properties = dict((registry_tool.parameters or {}).get("properties", {}) or {})
    required = set((registry_tool.parameters or {}).get("required", []) or [])
    field_defs: dict[str, tuple[Any, Any]] = {}

    for key, schema in properties.items():
        field_type = json_schema_to_python_type(schema)
        default = ... if key in required else None
        field_defs[key] = (field_type, default)

    args_schema = create_model(
        f"{name.title().replace('_', '')}Args",
        **field_defs,
    )  # type: ignore[call-overload]

    async def _runner(**kwargs: Any) -> str:
        return await service.tools.execute(name, kwargs)

    _runner.__name__ = name
    _runner.__doc__ = str(registry_tool.description or name)
    return StructuredTool.from_function(
        coroutine=_runner,
        name=name,
        description=str(registry_tool.description or name),
        args_schema=args_schema,
    )


def json_schema_to_python_type(schema: dict[str, Any] | None) -> Any:
    schema = schema or {}
    schema_type = str(schema.get("type", "string") or "string")
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        return list[Any]
    if schema_type == "object":
        return dict[str, Any]
    return str


def build_skill_paths(service: "CentralExecutor", *, virtual_mode: bool = False) -> list[str]:
    workspace = getattr(service.context, "workspace", None)
    paths: list[str] = []
    workspace_skills: Path | None = None

    if workspace is not None:
        workspace_skills = (Path(workspace) / "skills").resolve()

    builtin_skills = BUILTIN_SKILLS_DIR.resolve()
    if builtin_skills.exists():
        paths.append("/skills/builtin/" if virtual_mode else str(builtin_skills))
    if workspace_skills is not None and workspace_skills.exists():
        paths.append("/skills/workspace/" if virtual_mode else str(workspace_skills))

    return paths


__all__ = ["deep_agents_available", "ensure_agent"]
