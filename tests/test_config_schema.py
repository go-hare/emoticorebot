from __future__ import annotations

from emoticorebot.config.schema import Config


def test_config_uses_right_brain_mode_alias() -> None:
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "rightBrainMode": {
                        "model": "anthropic/claude-opus-4-5",
                        "provider": "openrouter",
                    }
                }
            }
        }
    )

    assert config.agents.defaults.right_brain_mode.model == "anthropic/claude-opus-4-5"


def test_config_dump_emits_right_brain_mode_only() -> None:
    dumped = Config().model_dump(by_alias=True)
    defaults = dumped["agents"]["defaults"]

    assert "rightBrainMode" in defaults
    assert "workerMode" not in defaults
    assert "leftBrainMode" in defaults
    assert "brainMode" not in defaults
