"""Configuration module for emoticorebot."""

from emoticorebot.config.loader import load_config, get_config_path
from emoticorebot.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
