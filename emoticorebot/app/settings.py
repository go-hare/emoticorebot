"""Runtime settings mapped from project config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from emoticorebot.config.schema import Config, ModelModeConfig, ProvidersConfig, ToolsConfig


@dataclass(slots=True)
class RuntimeSettings:
    workspace: Path
    front_mode: ModelModeConfig
    core_mode: ModelModeConfig
    providers: ProvidersConfig
    tools: ToolsConfig


def build_runtime_settings(config: Config) -> RuntimeSettings:
    return RuntimeSettings(
        workspace=config.workspace_path,
        front_mode=config.agents.defaults.brain_mode,
        core_mode=config.agents.defaults.executor_mode,
        providers=config.providers,
        tools=config.tools,
    )
