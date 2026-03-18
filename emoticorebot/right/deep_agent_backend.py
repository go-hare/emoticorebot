"""Deep Agent backend wiring for the execution layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from emoticorebot.right.skills import BUILTIN_SKILLS_DIR
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.task_result import TaskExecutionResult
from emoticorebot.utils.helpers import ensure_dir

try:
    from emoticorebot.checkpointing import PersistentMemorySaver
except Exception:
    PersistentMemorySaver = None

try:
    from langchain.agents import create_agent
except Exception:
    create_agent = None

try:
    from langchain.agents.middleware import HumanInTheLoopMiddleware, TodoListMiddleware
except Exception:
    HumanInTheLoopMiddleware = None
    TodoListMiddleware = None

try:
    from langgraph.checkpoint.memory import InMemorySaver
except Exception:
    InMemorySaver = None


class ExecutionAgentService(Protocol):
    _agent: Any | None
    _checkpointer: Any | None
    worker_llm: Any
    tools: Any
    context: Any
    tool_runtime: Any
    assistant_role: str


class WorkerStructuredResult(ProtocolModel):
    control_state: Literal["completed", "waiting_input", "failed"]
    status: Literal["success", "partial", "pending", "failed"]
    analysis: str = ""
    message: str = ""
    missing: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 0.8
    pending_review: list[dict[str, Any]] = Field(default_factory=list)
    attempt_count: int | None = None


@dataclass(frozen=True, slots=True)
class WorkerTaskProfile:
    name: Literal["general", "simple_file"]
    allow_exec: bool
    system_hint: str = ""
    task_hint: str = ""


GENERAL_TASK_PROFILE = WorkerTaskProfile(name="general", allow_exec=True)
_FILE_PATH_RE = re.compile(r"(?:^|[\s`'\"(])(?:[\w./-]+\.[A-Za-z0-9]{1,12})(?:$|[\s`'\"),])")
_FILE_TARGET_TERMS = (
    "文件",
    "file",
    "脚本",
    "script",
    "模块",
    "module",
)
_FILE_ACTION_TERMS = (
    "创建",
    "新建",
    "写入",
    "编辑",
    "修改",
    "生成",
    "补全",
    "create",
    "write",
    "edit",
    "update",
)
_EXEC_REQUIRED_TERMS = (
    "运行",
    "执行",
    "命令",
    "shell",
    "bash",
    "terminal",
    "终端",
    "测试",
    "test",
    "pytest",
    "unittest",
    "安装",
    "install",
    "pip ",
    "uv ",
    "poetry ",
    "npm ",
    "pnpm ",
    "yarn ",
    "docker",
    "git ",
    "make ",
    "build",
    "compile",
    "启动",
    "server",
)


def deep_agents_available() -> bool:
    return create_agent is not None


def build_task_profile(task_spec: dict[str, Any] | None) -> WorkerTaskProfile:
    if not isinstance(task_spec, dict):
        return GENERAL_TASK_PROFILE

    text_parts: list[str] = []
    for key in ("request", "goal", "expected_output", "history_context"):
        value = str(task_spec.get(key, "") or "").strip()
        if value:
            text_parts.append(value.lower())
    for key in ("constraints", "success_criteria", "skill_hints"):
        values = [
            str(item).strip().lower()
            for item in list(task_spec.get(key) or [])
            if str(item).strip()
        ]
        text_parts.extend(values)

    text = "\n".join(text_parts)
    if not text:
        return GENERAL_TASK_PROFILE

    has_file_target = bool(_FILE_PATH_RE.search(text)) or any(term in text for term in _FILE_TARGET_TERMS)
    has_file_action = any(term in text for term in _FILE_ACTION_TERMS)
    needs_exec = any(term in text for term in _EXEC_REQUIRED_TERMS)
    if not (has_file_target and has_file_action) or needs_exec:
        return GENERAL_TASK_PROFILE

    return WorkerTaskProfile(
        name="simple_file",
        allow_exec=False,
        system_hint=(
            "## 本次任务策略\n"
            "系统已将本次任务标记为简单文件任务。\n"
            "1. 本次不要使用 `exec`。\n"
            "2. 直接使用 `write_file` / `edit_file` / `read_file` 完成。\n"
            "3. 写入成功后只做一次最小验证，然后立即结束。\n\n"
        ),
        task_hint=(
            "执行策略（必须遵守）：这是一个简单文件任务。\n"
            "- 直接使用文件工具完成，不要运行 shell。\n"
            "- 不要为了确认结果而反复列目录、反复读取、或做多轮验证。\n"
            "- 写入成功后最多做一次轻量 readback，然后立刻返回。\n"
        ),
    )


def build_agent(
    service: ExecutionAgentService,
    *,
    profile: WorkerTaskProfile | None = None,
) -> Any:
    if create_agent is None:
        raise RuntimeError("langchain create_agent is not available")

    resolved_profile = profile or GENERAL_TASK_PROFILE
    tools = build_tools(service, profile=resolved_profile)
    checkpointer = ensure_checkpointer(service)
    middleware = build_agent_middleware()

    try:
        kwargs: dict[str, Any] = {
            "model": service.worker_llm,
            "tools": tools,
            "system_prompt": build_agent_instructions(service, profile=resolved_profile),
            "response_format": WorkerStructuredResult,
        }
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer
        if middleware:
            kwargs["middleware"] = middleware
        return create_agent(**kwargs)
    except TypeError as exc:
        raise RuntimeError(f"create_agent API mismatch: {exc}") from exc


def ensure_checkpointer(service: ExecutionAgentService) -> Any | None:
    if service._checkpointer is not None:
        return service._checkpointer
    workspace = Path(service.context.workspace).expanduser().resolve()
    checkpoint_dir = ensure_dir(workspace / "sessions" / "_checkpoints")
    role = str(getattr(service, "assistant_role", "worker") or "worker").strip()
    checkpoint_file = checkpoint_dir / f"{role}.pkl"
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
            "description": "请确认是否允许 worker 创建或修改定时任务。",
        },
    }


def build_agent_middleware() -> list[Any]:
    chain: list[Any] = []
    if HumanInTheLoopMiddleware is not None:
        chain.append(HumanInTheLoopMiddleware(interrupt_on=build_interrupt_on()))
    if TodoListMiddleware is not None:
        chain.append(TodoListMiddleware())
    return chain


def build_agent_instructions(
    service: ExecutionAgentService,
    *,
    profile: WorkerTaskProfile | None = None,
) -> str:
    workspace = Path(service.context.workspace).expanduser().resolve()
    role = str(getattr(service, "assistant_role", "worker") or "worker").strip()
    resolved_profile = profile or GENERAL_TASK_PROFILE
    return (
        f"你是 `{role}`，负责复杂问题的规划、执行与结果收口。\n"
        f"当前工作区目录是 `{workspace}`。\n\n"
        "文件工具一律使用相对工作区路径，例如 `src/foo.py`、`add.py`、`.timing_probe/demo.py`。\n"
        "不要传绝对路径 `/foo.py`，也不要把最终交付物写到 `/state/` 这类临时命名空间。\n"
        "创建或修改文件时，优先使用 `write_file` / `edit_file` / 行编辑工具，而不是 `exec`。\n\n"
        "`exec` 只在任务明确要求运行命令、安装依赖、启动进程、执行测试，或文件工具无法完成目标时才可使用。\n"
        "不要用 `exec` 去列目录、读取文件、cat 内容、或给简单文件任务做例行验证。\n\n"
        f"{resolved_profile.system_hint}"
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
        "2. 如果提示里给出了技能名，可以按需查看 `/skills/workspace/<skill>/SKILL.md` 或 `/skills/builtin/<skill>/SKILL.md`。\n"
        "3. 只有在技能确实匹配当前任务时才复用，不要机械套模板。\n\n"
        "## 审核钩子\n"
        "1. 在真正开始执行前，必须先调用一次 `audit_tool`。\n"
        "2. 当你判断“任务可以开始”时，调用 `audit_tool(decision=\"accept\", ...)`。\n"
        "3. 当你判断“不应执行”时，调用 `audit_tool(decision=\"reject\", ...)`。\n"
        "4. 当你判断“不需要执行，只需要给左脑理性答案素材”时，调用 `audit_tool(decision=\"answer_only\", ...)`。\n"
        "5. `reject / answer_only` 会直接终止本次 run，所以要把理由或答案素材写清楚。\n\n"
        "## 阶段通知\n"
        "1. 当你完成关键里程碑时，必须调用 `report_stage` 汇报一次。\n"
        "2. 尤其是创建文件、修改文件、完成主要验证之后，要立刻汇报。\n"
        "3. 不要为每个微小动作都汇报，只在用户真正关心的节点汇报。\n\n"
        "## 收口原则\n"
        "1. 简单文件任务在写入成功后，只做一次最小验证，然后立即返回 `completed`。\n"
        "2. 不要为了润色答案而重复调用 `exec`、重复读写同一个文件、或做多轮无意义校验。\n"
        "3. 如果 `write_file` / `edit_file` 已成功，且一次 readback 或一次轻量检查通过，就直接结束。\n\n"
        "## Task 结构化输出要求\n"
        "你必须且只能输出一个合法的 JSON 对象（不要包裹在 markdown 代码块中），"
        "严格遵循以下 `TaskExecutionResult` schema：\n"
        "```json\n"
        "{\n"
        '  "control_state": "<enum: completed | waiting_input | failed>",\n'
        '  "status": "<enum: success | partial | pending | failed>",\n'
        '  "analysis": "<string: 紧凑结论，不展开冗长推理>",\n'
        '  "message": "<string: 最终可交付结果或失败原因>",\n'
        '  "missing": "<array: 缺失信息列表；没有就填空数组>",\n'
        '  "recommended_action": "<string: 建议补充项；没有就填空字符串>",\n'
        '  "confidence": "<number: 0.0-1.0 置信度>",\n'
        '  "pending_review": "<array: 需要审核的项；没有就填空数组>"\n'
        "}\n"
        "```\n"
        "⚠️ 重要约束：\n"
        "- 输出必须是可被 `json.loads()` 直接解析的纯 JSON，不要输出任何 JSON 之外的文字。\n"
        "- 已完成任务：使用 `completed`，并在 `message` 中写最终可交付结果。\n"
        "- 缺少关键信息但任务仍可恢复：使用 `waiting_input`，并在 `missing` / `recommended_action` 中写清楚缺什么。\n"
        "- 无法恢复或执行报错：使用 `failed`，在 `message` 或 `analysis` 中说明原因。\n"
        "- `task_trace` 由系统补充，你不要自己填写。\n"
    )


def build_backend(service: ExecutionAgentService) -> Any | None:
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


def build_tools(
    service: ExecutionAgentService,
    *,
    profile: WorkerTaskProfile | None = None,
) -> list[Any]:
    tools: list[Any] = []
    audit_tool = build_audit_tool(service)
    if audit_tool is not None:
        tools.append(audit_tool)
    stage_tool = build_stage_report_tool(service)
    if stage_tool is not None:
        tools.append(stage_tool)
    if service.tools is not None:
        resolved_profile = profile or GENERAL_TASK_PROFILE
        tool_names = [name for name in service.tools.tool_names if name != "message"]
        if not resolved_profile.allow_exec:
            tool_names = [name for name in tool_names if name != "exec"]
        tools.extend(build_registry_tools(service, tool_names))
    return tools


def build_audit_tool(service: ExecutionAgentService) -> Any | None:
    """构建右脑审核钩子，决定本次 run 是否继续执行。"""
    try:
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field
    except Exception:
        return None

    class AuditArgs(BaseModel):
        decision: Literal["accept", "answer_only", "reject"] = Field(description="本次审核裁决。")
        reason: str = Field(default="", description="裁决理由。")
        summary: str = Field(default="", description="给左脑看的紧凑摘要，可选。")
        result_text: str = Field(default="", description="decision=answer_only 时返回给左脑的答案素材。")

    async def audit_tool(decision: str, reason: str = "", summary: str = "", result_text: str = "") -> str:
        return await service.tool_runtime.audit(
            decision=str(decision or "").strip(),  # type: ignore[arg-type]
            reason=reason,
            summary=summary,
            result_text=result_text,
            event="task.audit",
            producer=str(getattr(service, "assistant_role", "worker") or "worker").strip(),
        )

    return StructuredTool.from_function(
        coroutine=audit_tool,
        name="audit_tool",
        description=(
            "右脑审核钩子。必须在真正开始执行前先调用一次。"
            "`accept` 表示任务可以开始；`reject` 表示不应执行；"
            "`answer_only` 表示不需要执行，只返回理性答案素材给左脑。"
        ),
        args_schema=AuditArgs,
    )


def build_stage_report_tool(service: ExecutionAgentService) -> Any | None:
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
        role = str(getattr(service, "assistant_role", "worker") or "worker").strip()
        return await service.tool_runtime.report_progress(
            str(summary or "").strip(),
            event="task.stage",
            producer=role,
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
        result = await service.tools.execute(name, kwargs)
        summary = summarize_tool_progress(name=name, result=result)
        if summary:
            role = str(getattr(service, "assistant_role", "worker") or "worker").strip()
            await service.tool_runtime.report_progress(
                summary,
                event="task.tool",
                producer=role,
                phase="tool",
                tool_name=name,
            )
        return result

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


def summarize_tool_progress(*, name: str, result: Any) -> str:
    watched = {
        "write_file",
        "edit_file",
        "insert_lines",
        "replace_lines",
        "delete_lines",
        "exec",
    }
    if name not in watched:
        return ""

    text = str(result or "").strip()
    if not text or text.startswith("Error:"):
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    headline = lines[0]
    if name == "exec" and headline.lower().startswith("exit code:"):
        if len(lines) > 1:
            headline = f"{headline}; {lines[1]}"
    return f"{name} 已完成：{headline[:160]}"


def build_skill_paths(service: ExecutionAgentService, *, virtual_mode: bool = False) -> list[str]:
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


__all__ = ["GENERAL_TASK_PROFILE", "WorkerTaskProfile", "build_agent", "build_task_profile", "deep_agents_available", "ensure_checkpointer"]
