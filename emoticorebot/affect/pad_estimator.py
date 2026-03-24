"""Very small user-text to PAD estimator for the outer affect layer."""

from __future__ import annotations

from emoticorebot.affect.models import PADVector

_POSITIVE_PLEASURE = (
    "开心",
    "高兴",
    "喜欢",
    "太好了",
    "棒",
    "爱",
    "谢谢",
    "舒服",
    "温暖",
    "happy",
    "love",
    "great",
    "awesome",
    "nice",
)
_NEGATIVE_PLEASURE = (
    "累",
    "难受",
    "焦虑",
    "压力",
    "崩溃",
    "烦",
    "难过",
    "低落",
    "痛苦",
    "不想",
    "讨厌",
    "sad",
    "stress",
    "tired",
    "overwhelmed",
    "angry",
)
_HIGH_AROUSAL = (
    "急",
    "马上",
    "快",
    "激动",
    "兴奋",
    "赶紧",
    "炸了",
    "啊啊",
    "urgent",
    "asap",
    "panic",
    "excited",
)
_LOW_AROUSAL = (
    "困",
    "晚安",
    "睡",
    "休息",
    "安静",
    "躺平",
    "发呆",
    "calm",
    "sleepy",
)
_HIGH_DOMINANCE = (
    "我来",
    "搞定",
    "安排",
    "决定",
    "能行",
    "必须",
    "直接",
    "掌控",
    "done",
    "fix",
    "ship",
)
_LOW_DOMINANCE = (
    "不会",
    "不知道",
    "救命",
    "帮帮我",
    "怎么办",
    "失控",
    "卡住",
    "不行",
    "没办法",
    "help",
    "stuck",
    "can't",
)
_DIRECT_HELP = (
    "帮我",
    "看一下",
    "看看",
    "处理一下",
    "查一下",
    "please",
)


def estimate_user_pad(user_text: str) -> PADVector:
    """Estimate the user's immediate PAD signal from raw text."""

    text = str(user_text or "").strip().lower()
    if not text:
        return PADVector()

    positive_hits = _count_hits(text, _POSITIVE_PLEASURE)
    negative_hits = _count_hits(text, _NEGATIVE_PLEASURE)
    high_arousal_hits = _count_hits(text, _HIGH_AROUSAL)
    low_arousal_hits = _count_hits(text, _LOW_AROUSAL)
    high_dominance_hits = _count_hits(text, _HIGH_DOMINANCE)
    low_dominance_hits = _count_hits(text, _LOW_DOMINANCE)
    question_count = min(2, text.count("?") + text.count("？"))
    exclaim_count = min(3, text.count("!") + text.count("！"))
    direct_help = any(keyword in text for keyword in _DIRECT_HELP)

    pleasure = 0.24 * positive_hits - 0.28 * negative_hits
    arousal = 0.20 * high_arousal_hits - 0.18 * low_arousal_hits + 0.08 * exclaim_count
    dominance = 0.22 * high_dominance_hits - 0.26 * low_dominance_hits

    if negative_hits and high_arousal_hits:
        arousal += 0.10
    if positive_hits and high_arousal_hits:
        pleasure += 0.06
        arousal += 0.05
    if negative_hits and low_arousal_hits:
        pleasure -= 0.08
        arousal -= 0.06
    if direct_help:
        dominance -= 0.08
    if question_count:
        dominance -= 0.04 * question_count
        arousal += 0.03 * question_count
    if len(text) <= 4:
        arousal -= 0.04

    return PADVector(pleasure=pleasure, arousal=arousal, dominance=dominance)


def _count_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)
