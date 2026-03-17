"""IO-layer exports for stable input normalization."""

from emoticorebot.io.adapters import build_stable_input, build_text_input, build_video_input, build_voice_input
from emoticorebot.io.models import NormalizedInput
from emoticorebot.io.normalizer import InputNormalizer

__all__ = [
    "InputNormalizer",
    "NormalizedInput",
    "build_stable_input",
    "build_text_input",
    "build_voice_input",
    "build_video_input",
]
