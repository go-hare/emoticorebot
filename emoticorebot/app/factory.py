"""Build the front-core runtime."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from emoticorebot.app.settings import RuntimeSettings, build_runtime_settings
from emoticorebot.config.loader import load_config
from emoticorebot.config.schema import Config
from emoticorebot.core.runtime import CoreRuntime
from emoticorebot.execution.runtime import ExecutionRuntime
from emoticorebot.front.service import FrontService
from emoticorebot.gateway.service import GatewayService
from emoticorebot.providers.factory import LLMFactory
from emoticorebot.state import CurrentStateStore, MemoryStore, WorldStateStore
from emoticorebot.utils.helpers import ensure_dir


@dataclass(slots=True)
class AppContext:
    settings: RuntimeSettings
    gateway: GatewayService


def build_app_context(config: Config | None = None) -> AppContext:
    config = config or load_config()
    settings = build_runtime_settings(config)
    ensure_workspace_layout(settings.workspace)
    validate_runtime_settings(settings)

    factory = LLMFactory(
        providers_config=settings.providers,
        executor_mode=settings.execution_mode,
        brain_mode=settings.front_mode,
    )
    front_model = factory.get_brain()
    core_model = factory.get_executor()
    reflection_model = factory.get_executor()
    execution_model = factory.get_executor()

    memory_store = MemoryStore(settings.workspace, settings.memory, settings.providers)
    world_state_store = WorldStateStore(settings.workspace)
    current_state_store = CurrentStateStore(settings.workspace)
    current_state_store.ensure("# 当前状态\n\n- 平静\n")
    memory_store.refresh_vector_mirror()

    gateway_placeholder: dict[str, GatewayService] = {}

    async def speak_handler(thread_id: str, intent_text: str) -> None:
        await gateway_placeholder["gateway"].send_followup(thread_id, intent_text)

    async def dispatch_handler(checks: list[dict]) -> None:
        await gateway_placeholder["gateway"].dispatch_checks(checks)

    async def reflection_handler(request) -> None:
        await gateway_placeholder["gateway"].launch_reflection(request)

    core_runtime = CoreRuntime(
        workspace=settings.workspace,
        main_model=core_model,
        reflection_model=reflection_model,
        memory_store=memory_store,
        world_state_store=world_state_store,
        speak_handler=speak_handler,
        dispatch_handler=dispatch_handler,
        reflection_handler=reflection_handler,
    )
    execution_runtime = ExecutionRuntime(
        workspace=settings.workspace,
        model=execution_model,
        tools_config=settings.tools,
        result_handler=lambda payload: gateway_placeholder["gateway"].handle_execution_result(payload),
    )
    front_service = FrontService(settings.workspace, front_model)
    gateway = GatewayService(
        workspace=settings.workspace,
        front=front_service,
        core=core_runtime,
        execution=execution_runtime,
        memory_store=memory_store,
        world_state_store=world_state_store,
    )
    gateway_placeholder["gateway"] = gateway
    return AppContext(settings=settings, gateway=gateway)


def validate_runtime_settings(settings: RuntimeSettings) -> None:
    front_provider = resolve_provider_name(settings.front_mode)
    core_provider = resolve_provider_name(settings.core_mode)
    execution_provider = resolve_provider_name(settings.execution_mode)
    for label, provider in (
        ("front", front_provider),
        ("core", core_provider),
        ("execution", execution_provider),
    ):
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
    ensure_workspace_file(workspace, "current_state.md", workspace)
    ensure_workspace_file(workspace, "drive_config.yaml", workspace)
    ensure_workspace_file(workspace, "FRONT.md", workspace / "templates")
    ensure_workspace_file(workspace, "FRONT_FOLLOWUP.md", workspace / "templates")
    ensure_workspace_file(workspace, "CORE_MAIN.md", workspace / "templates")
    ensure_workspace_file(workspace, "CORE_REFLECTION.md", workspace / "templates")
    ensure_workspace_file(workspace, "EXECUTION.md", workspace / "templates")


def ensure_workspace_file(workspace: Path, name: str, target_dir: Path) -> None:
    package_root = files("emoticorebot") / "templates"
    source = package_root / name
    target = target_dir / name
    if target.exists():
        return
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def resolve_provider_name(mode) -> str:
    provider = str(mode.provider or "auto").strip().lower() or "auto"
    if provider != "auto":
        return provider
    model = str(mode.model or "").strip().lower()
    if "/" in model:
        return "openrouter"
    if model.startswith(("claude-", "claude.")):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return "openai"
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith(("llama", "mistral", "mixtral", "gemma", "qwen-", "qwen2", "qwen3", "deepseek-")):
        return "openai"
    return "openai"
