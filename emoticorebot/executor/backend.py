"""Agent wiring for the executor layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from emoticorebot.protocol.task_models import ProtocolModel

try:
    from langchain.agents import create_agent
except Exception:
    create_agent = None


class ExecutionAgentService(Protocol):
    executor_llm: Any
    tools: Any
    context: Any
    assistant_role: str


class ExecutionResultSchema(ProtocolModel):
    control_state: Literal["completed", "failed"]
    status: Literal["success", "partial", "failed"]
    analysis: str = ""
    message: str = ""


def backend_available() -> bool:
    return create_agent is not None


def build_agent(
    service: ExecutionAgentService,
) -> Any:
    if create_agent is None:
        raise RuntimeError("langchain create_agent is not available")

    tools = build_agent_tools(service)

    try:
        kwargs: dict[str, Any] = {
            "model": service.executor_llm,
            "tools": tools,
            "system_prompt": build_prompt(service),
            "response_format": ExecutionResultSchema,
        }
        return create_agent(**kwargs)
    except TypeError as exc:
        raise RuntimeError(f"create_agent API mismatch: {exc}") from exc


def build_prompt(
    service: ExecutionAgentService,
) -> str:
    workspace = Path(service.context.workspace).expanduser().resolve()
    role = str(getattr(service, "assistant_role", "executor") or "executor").strip()
    return (
        f"你是 `{role}`，负责复杂问题的规划、执行与结果收口。\n"
        f"当前工作区目录是 `{workspace}`。\n\n"
        "文件工具一律使用相对工作区路径，例如 `src/foo.py`、`add.py`、`.timing_probe/demo.py`。\n"
        "不要传绝对路径 `/foo.py`。\n"
        "创建或修改文件时，优先使用 `write_file` / `edit_file` / 行编辑工具，而不是 `exec`。\n\n"
        "`exec` 只在任务明确要求运行命令、安装依赖、启动进程、执行测试，或文件工具无法完成目标时才可使用。\n"
        "如果必须使用 `exec`，默认就在当前工作区目录执行；不要传 `working_dir=\".\"` 这类会制造歧义的值。\n"
        "不要用 `exec` 去列目录、读取文件、cat 内容、或做例行验证。\n\n"
        "## 职责\n"
        "1. 接收委托的问题并执行。\n"
        "2. 必要时调用工具，在单次执行内完成任务。\n"
        "3. 给出清晰结论。\n\n"
        "## 边界\n"
        "1. 不负责最终对用户表达，只返回执行结果。\n"
        "2. 不负责人格维护、情绪陪伴。\n"
        "3. 不要输出原始日志、工具轨迹或 JSON 代码块。\n\n"
        "## 技能使用\n"
        "1. 如果任务上下文里带有 `skill_hints`，优先按这些提示执行。\n"
        "2. 只有在提示里已经给出明确技能或路径时才去读取，不要主动枚举一堆技能目录。\n"
        "3. 只有在技能确实匹配当前任务时才复用，不要机械套模板。\n\n"
        "## 执行原则\n"
        "1. 收到当前 check 后，直接开始执行，不需要额外审核或等待确认。\n"
        "2. 如果信息不足或工具失败，优先在当前 run 内换一种做法继续推进。\n"
        "3. 只有在确实无法继续时，才返回 `failed`，把缺失项或失败原因写清楚。\n"
        "4. 不支持中途等待用户批准、补充或继续；当前 run 只能继续执行或直接结束。\n\n"
        "## 回传约束\n"
        "1. 你的对话、工具调用和工具结果会被系统自动采集并回传给大脑。\n"
        "2. 不需要额外调用专门的阶段汇报工具。\n"
        "3. 正常推进执行，关键进展体现在你的实际操作和最终结构化结果里即可。\n\n"
        "## 收口原则\n"
        "1. 写入成功后，只做一次最小验证，然后立即返回 `completed`。\n"
        "2. 不要为了润色答案而重复调用 `exec`、重复读写同一个文件、或做多轮无意义校验。\n"
        "3. 如果 `write_file` / `edit_file` 已成功，且一次 readback 或一次轻量检查通过，就直接结束。\n\n"
        "## 执行结果结构化输出要求\n"
        "你必须且只能输出一个合法的 JSON 对象（不要包裹在 markdown 代码块中），"
        "严格遵循以下执行结果 schema：\n"
        "```json\n"
        "{\n"
        '  "control_state": "<enum: completed | failed>",\n'
        '  "status": "<enum: success | partial | failed>",\n'
        '  "analysis": "<string: 紧凑结论，不展开冗长推理>",\n'
        '  "message": "<string: 最终可交付结果或失败原因>"\n'
        "}\n"
        "```\n"
        "⚠️ 重要约束：\n"
        "- 输出必须是可被 `json.loads()` 直接解析的纯 JSON，不要输出任何 JSON 之外的文字。\n"
        "- 已完成任务：使用 `completed`，并在 `message` 中写最终可交付结果。\n"
        "- 缺少关键信息或无法恢复时：使用 `failed`，把缺失项或失败原因写清楚。\n"
        "- 无法恢复或执行报错：使用 `failed`，在 `message` 或 `analysis` 中说明原因。\n"
        "- `task_trace` 由系统补充，你不要自己填写。\n"
    )


def build_agent_tools(
    service: ExecutionAgentService,
) -> list[Any]:
    tools: list[Any] = []
    if service.tools is not None:
        tool_names = [name for name in service.tools.tool_names if name != "message"]
        tools.extend(build_registry_tools(service, tool_names))
    return tools


def build_registry_tools(service: ExecutionAgentService, names: list[str]) -> list[Any]:
    if service.tools is None:
        return []

    built: list[Any] = []
    for name in names:
        tool = build_registry_tool(service, name)
        if tool is not None:
            built.append(tool)
    return built


def build_registry_tool(service: ExecutionAgentService, name: str) -> Any | None:
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


__all__ = [
    "ExecutionResultSchema",
    "build_agent",
    "build_prompt",
    "build_registry_tool",
    "build_registry_tools",
    "build_agent_tools",
    "backend_available",
]
