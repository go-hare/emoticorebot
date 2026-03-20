from __future__ import annotations

import pytest

from emoticorebot.config.schema import Config


def test_config_uses_main_brain_and_execution_modes() -> None:
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "mainBrainMode": {
                        "model": "anthropic/claude-sonnet-4-5",
                        "provider": "openrouter",
                    },
                    "executionMode": {
                        "model": "anthropic/claude-opus-4-5",
                        "provider": "openrouter",
                    },
                }
            }
        }
    )

    assert config.agents.defaults.main_brain_mode.model == "anthropic/claude-sonnet-4-5"
    assert config.agents.defaults.execution_mode.model == "anthropic/claude-opus-4-5"


def test_config_dump_emits_main_brain_and_execution_modes_only() -> None:
    dumped = Config().model_dump(by_alias=True)
    defaults = dumped["agents"]["defaults"]

    assert "mainBrainMode" in defaults
    assert "executionMode" in defaults
    assert "leftBrainMode" not in defaults
    assert "rightBrainMode" not in defaults


def test_config_rejects_legacy_brain_mode_keys() -> None:
    with pytest.raises(Exception):
        Config.model_validate({"agents": {"defaults": {"leftBrainMode": {"model": "x", "provider": "y"}}}})

    with pytest.raises(Exception):
        Config.model_validate({"agents": {"defaults": {"rightBrainMode": {"model": "x", "provider": "y"}}}})
