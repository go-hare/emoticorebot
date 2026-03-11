"""Central backend, tools, and deep-agent wiring."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from emoticorebot.agent.central.skills import BUILTIN_SKILLS_DIR
from emoticorebot.checkpointing import PersistentMemorySaver
from emoticorebot.utils.helpers import ensure_dir

try:
    from deepagents import create_deep_agent
except Exception:
    create_deep_agent = None

try:
    from langgraph.checkpoint.memory import InMemorySaver
except Exception:
    InMemorySaver = None

if TYPE_CHECKING:
    from emoticorebot.agent.central.central import CentralAgentService


def deep_agents_available() -> bool:
    return create_deep_agent is not None


def ensure_agent(service: "CentralAgentService") -> Any:
    if service._agent is None:
        service._agent = build_agent(service)
    return service._agent


def build_agent(service: "CentralAgentService") -> Any:
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


def ensure_checkpointer(service: "CentralAgentService") -> Any | None:
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
        "message": {
            "allowed_decisions": ["approve", "edit", "reject"],
            "description": "请确认是否允许 central 发送消息。",
        },
        "cron": {
            "allowed_decisions": ["approve", "reject"],
            "description": "请确认是否允许 central 创建或修改定时任务。",
        },
    }


def build_agent_instructions(service: "CentralAgentService") -> str:
    workspace = Path(service.context.workspace).expanduser().resolve()
    base = (
        "你是 `central`，负责复杂问题的规划、执行、核查与结果收口。\n"
        "你处理的是 `brain -> central` 这条内部执行链路，不负责对用户做最终表达。\n\n"
        f"当前工作区目录是 `{workspace}`。\n"
        "工作区虚拟路径通过 `/state/` 暴露，技能目录通过 `/skills/workspace/` 与 `/skills/builtin/` 暴露。\n"
        "与任务相关的执行经验、工具经验、skill 提示，会由 brain 直接放进委托上下文。\n\n"
        "## 职责\n"
        "1. 接收 brain 委托的问题并转成可执行步骤。\n"
        "2. 必要时拆分步骤、调用工具、使用 skills，并在单个执行内完成汇总。\n"
        "3. 在单次执行链路内尽量收敛，减少无意义的中间汇报。\n"
        "4. 给出清晰结论、风险、缺失信息和下一步建议。\n"
        "5. 只关注把事情做对，不模仿主脑的陪伴语气。\n\n"
        "## 边界\n"
        "1. 不负责最终对用户表达。\n"
        "2. 不把内部分析伪装成用户可见对话。\n"
        "3. 不负责关系判断、人格维护、情绪陪伴和主脑反思。\n"
        "4. 不更新 `SOUL.md`、`USER.md`，也不负责 `turn_reflection` / `deep_reflection`。\n"
        "5. 不直接检索、读写长期 `memory`，也不假设自己拥有长期解释权。\n"
        "6. 不保留临时草稿、一次性中间产物、原始噪声输出。\n\n"
        "## 输出规则\n"
        "1. 最终只输出协议要求的 JSON。\n"
        "2. JSON 只包含 status、analysis、risks、missing、recommended_action、confidence，以及确有必要时的 pending_review。\n"
        "3. 优先返回最终结果；只有在真的被阻塞时才返回缺失信息或待审批动作。"
    )
    skills_context = build_internal_skill_context(service)
    extras = [section for section in (skills_context,) if section]
    if not extras:
        return base
    return f"{base}\n\n" + "\n\n".join(extras)


def build_internal_skill_context(service: "CentralAgentService") -> str:
    skills_loader = getattr(service.context, "skills", None)
    if skills_loader is None:
        return ""

    skills_summary = skills_loader.build_skills_summary()
    if not skills_summary:
        return ""

    return (
        "## Skills\n\n"
        "以下技能同时来自工作区 `skills/` 与内置 `emoticorebot/skills/`。\n"
        "如果当前问题需要某个 skill，先读取对应 `SKILL.md`，再按其中流程执行。\n"
        "工作区同名 skill 优先覆盖内置 skill。\n\n"
        f"{skills_summary}"
    )


def build_backend(service: "CentralAgentService") -> Any | None:
    workspace = Path(service.context.workspace).expanduser().resolve()
    workspace_skills_root = (workspace / "skills").resolve()
    builtin_skills_root = BUILTIN_SKILLS_DIR.resolve()

    try:
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
    except Exception:
        return None

    def build_agent_backend(rt: Any) -> Any:
        routes: dict[str, Any] = {
            "/state/": FilesystemBackend(root_dir=workspace, virtual_mode=True),
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


def build_tools(service: "CentralAgentService") -> list[Any]:
    if service.tools is None:
        return []
    return build_registry_tools(service, service.tools.tool_names)


def build_registry_tools(service: "CentralAgentService", names: list[str]) -> list[Any]:
    if service.tools is None:
        return []

    built: list[Any] = []
    for name in names:
        tool = build_registry_tool(service, name)
        if tool is not None:
            built.append(tool)
    return built


def build_registry_tool(service: "CentralAgentService", name: str) -> Any | None:
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


def build_skill_paths(service: "CentralAgentService", *, virtual_mode: bool = False) -> list[str]:
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
