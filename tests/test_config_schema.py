from __future__ import annotations

from emoticorebot.config.schema import Config


def test_config_uses_worker_mode_alias() -> None:
    config = Config.model_validate(
        {
            "agents": {
                "defaults": {
                    "workerMode": {
                        "model": "anthropic/claude-opus-4-5",
                        "provider": "openrouter",
                    }
                }
            }
        }
    )

    assert config.agents.defaults.worker_mode.model == "anthropic/claude-opus-4-5"


def test_config_dump_emits_worker_mode_only() -> None:
    dumped = Config().model_dump(by_alias=True)
    defaults = dumped["agents"]["defaults"]

    assert "workerMode" in defaults
    assert "centralMode" not in defaults
