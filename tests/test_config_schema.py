from __future__ import annotations

from emoticorebot.config.schema import Config


def test_config_uses_executor_mode_alias() -> None:
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "executorMode": {
                        "model": "anthropic/claude-opus-4-5",
                        "provider": "openrouter",
                    }
                }
            }
        }
    )

    assert config.agents.defaults.executor_mode.model == "anthropic/claude-opus-4-5"


def test_config_dump_emits_brain_and_executor_modes() -> None:
    dumped = Config().model_dump(by_alias=True)
    defaults = dumped["agents"]["defaults"]

    assert "executorMode" in defaults
    assert "brainMode" in defaults
    assert "rightBrainMode" not in defaults
    assert "leftBrainMode" not in defaults
