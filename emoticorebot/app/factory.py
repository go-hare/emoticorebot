"""Build the front + resident-kernel application."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from emoticorebot.affect import create_affect_runtime
from emoticorebot.app.settings import RuntimeSettings, build_runtime_settings
from emoticorebot.brain_kernel import (
    BrainKernel,
    JsonlMemoryStore,
    SleepAgent,
)
from emoticorebot.config.loader import load_config
from emoticorebot.config.schema import Config
from emoticorebot.front.service import FrontService
from emoticorebot.providers.factory import LLMFactory, resolve_provider_name
from emoticorebot.runtime.scheduler import RuntimeScheduler
from emoticorebot.tools.exec_tool import ExecTool
from emoticorebot.tools.file_tools import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
)
from emoticorebot.tools.web_tools import WebFetchTool, WebSearchTool
from emoticorebot.utils.helpers import ensure_dir


@dataclass(slots=True)
class AppContext:
    settings: RuntimeSettings
    runtime: RuntimeScheduler


def build_app_context(config: Config | None = None) -> AppContext:
    config = config or load_config()
    settings = build_runtime_settings(config)
    ensure_workspace_layout(settings.workspace)
    validate_runtime_settings(settings)

    front_factory = LLMFactory(
        providers_config=settings.providers,
        executor_mode=settings.core_mode,
        brain_mode=settings.front_mode,
    )
    memory_store = JsonlMemoryStore(settings.workspace)

    front_service = FrontService(settings.workspace, front_factory.get_brain())
    sleep_agent = SleepAgent(memory_store=memory_store)
    kernel = BrainKernel(
        agent_id="emoticorebot",
        model=front_factory.get_executor(),
        tools=build_kernel_tools(settings),
        memory_store=memory_store,
        sleep_agent=sleep_agent,
        max_steps=settings.core_mode.max_tool_iterations,
    )
    project_model_path = Path(__file__).resolve().parents[2] / "mode" / "Chordia"
    affect_runtime = create_affect_runtime(settings.workspace, project_model_path)
    runtime = RuntimeScheduler(
        workspace=settings.workspace,
        front=front_service,
        kernel=kernel,
        affect_runtime=affect_runtime,
    )
    return AppContext(settings=settings, runtime=runtime)


def build_kernel_tools(settings: RuntimeSettings) -> list[Any]:
    allowed_dir = settings.workspace if settings.tools.restrict_to_workspace else None
    return [
        ReadFileTool(settings.workspace, allowed_dir=allowed_dir),
        WriteFileTool(settings.workspace, allowed_dir=allowed_dir),
        EditFileTool(settings.workspace, allowed_dir=allowed_dir),
        ListDirTool(settings.workspace, allowed_dir=allowed_dir),
        SearchFilesTool(settings.workspace, allowed_dir=allowed_dir),
        ExecTool(
            timeout=settings.tools.exec.timeout,
            working_dir=str(settings.workspace),
            restrict_to_workspace=settings.tools.restrict_to_workspace,
            path_append=settings.tools.exec.path_append,
        ),
        WebSearchTool(api_key=settings.tools.web.search.api_key or None),
        WebFetchTool(),
    ]


def validate_runtime_settings(settings: RuntimeSettings) -> None:
    for label, mode in (
        ("front", settings.front_mode),
        ("core", settings.core_mode),
    ):
        provider = resolve_provider_name(mode)
        if provider == "ollama":
            continue
        provider_config = getattr(settings.providers, provider, None)
        api_key = str(getattr(provider_config, "api_key", "") or "").strip()
        if api_key:
            continue
        raise RuntimeError(f"{label} model provider '{provider}' is not configured with an api_key")


def ensure_workspace_layout(workspace: Path) -> None:
    ensure_dir(workspace)
    ensure_dir(workspace / "templates")
    ensure_dir(workspace / "memory")
    ensure_dir(workspace / "memony")
    ensure_dir(workspace / "session")
    ensure_dir(workspace / "skills")
    ensure_workspace_file(workspace, "USER.md", workspace)
    ensure_workspace_file(workspace, "SOUL.md", workspace)
    ensure_workspace_file(workspace, "AGENTS.md", workspace)
    ensure_workspace_file(workspace, "TOOLS.md", workspace)
    ensure_workspace_file(workspace, "HEARTBEAT.md", workspace)
    ensure_workspace_file(workspace, "drive_config.yaml", workspace)
    ensure_workspace_file(workspace, "FRONT.md", workspace / "templates")


def ensure_workspace_file(workspace: Path, name: str, target_dir: Path) -> None:
    package_root = files("emoticorebot") / "templates"
    source = package_root / name
    target = target_dir / name
    if target.exists():
        return
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
