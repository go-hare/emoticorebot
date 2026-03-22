"""Build the Front -> Scheduler -> Core application."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from emoticorebot.app.settings import RuntimeSettings, build_runtime_settings
from emoticorebot.config.loader import load_config
from emoticorebot.config.schema import Config
from emoticorebot.core.runtime import CoreRuntime
from emoticorebot.front.service import FrontService
from emoticorebot.providers.factory import AgentsModelFactory, LLMFactory, resolve_provider_name
from emoticorebot.runtime.scheduler import RuntimeScheduler
from emoticorebot.sleep.runtime import SleepRuntime
from emoticorebot.state import CurrentStateStore, MemoryStore, SkillStore, WorldModelStore
from emoticorebot.state.schemas import WorldModel
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
    agents_factory = AgentsModelFactory(settings.providers)

    memory_store = MemoryStore(settings.workspace, settings.memory, settings.providers)
    world_model_store = WorldModelStore(settings.workspace)
    current_state_store = CurrentStateStore(settings.workspace)
    skill_store = SkillStore(settings.workspace)

    current_state_store.ensure("# 当前状态\n\n- 平静\n")
    if not (settings.workspace / "state" / "world_model.json").exists():
        world_model_store.save(WorldModel())
    memory_store.refresh_vector_mirror()

    sleep_runtime = SleepRuntime(
        workspace=settings.workspace,
        model_bundle=agents_factory.build(settings.sleep_mode),
    )
    front_service = FrontService(settings.workspace, front_factory.get_brain())
    core_runtime = CoreRuntime(
        workspace=settings.workspace,
        model_bundle=agents_factory.build(settings.core_mode),
        mode=settings.core_mode,
        memory_store=memory_store,
        world_model_store=world_model_store,
        current_state_store=current_state_store,
        skill_store=skill_store,
        tools_config=settings.tools,
        sleep_runtime=sleep_runtime,
    )
    runtime = RuntimeScheduler(
        workspace=settings.workspace,
        front=front_service,
        core=core_runtime,
        memory_store=memory_store,
        world_model_store=world_model_store,
    )
    return AppContext(settings=settings, runtime=runtime)


def validate_runtime_settings(settings: RuntimeSettings) -> None:
    for label, mode in (
        ("front", settings.front_mode),
        ("core", settings.core_mode),
        ("sleep", settings.sleep_mode),
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
    ensure_dir(workspace / "session")
    ensure_dir(workspace / "state")
    ensure_dir(workspace / "skills")
    ensure_workspace_file(workspace, "USER.md", workspace)
    ensure_workspace_file(workspace, "SOUL.md", workspace)
    ensure_workspace_file(workspace, "AGENTS.md", workspace)
    ensure_workspace_file(workspace, "TOOLS.md", workspace)
    ensure_workspace_file(workspace, "HEARTBEAT.md", workspace)
    ensure_workspace_file(workspace, "current_state.md", workspace)
    ensure_workspace_file(workspace, "drive_config.yaml", workspace)
    ensure_workspace_file(workspace, "FRONT.md", workspace / "templates")
    ensure_workspace_file(workspace, "CORE_MAIN.md", workspace / "templates")
    ensure_workspace_file(workspace, "EXECUTOR.md", workspace / "templates")
    ensure_workspace_file(workspace, "REFLECTION.md", workspace / "templates")
    ensure_workspace_file(workspace, "SLEEP.md", workspace / "templates")


def ensure_workspace_file(workspace: Path, name: str, target_dir: Path) -> None:
    package_root = files("emoticorebot") / "templates"
    source = package_root / name
    target = target_dir / name
    if target.exists():
        return
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
