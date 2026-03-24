"""Rule-based first-pass companion intent builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from emoticorebot.companion.models import CompanionIntent

if TYPE_CHECKING:
    from emoticorebot.affect import AffectState, EmotionSignal

_COMFORT_KEYWORDS = (
    "累",
    "难受",
    "烦",
    "崩溃",
    "压力",
    "焦虑",
    "难过",
    "低落",
    "想哭",
    "不想",
    "tired",
    "sad",
    "stress",
    "overwhelmed",
)
_ENCOURAGE_KEYWORDS = (
    "成功",
    "太好了",
    "开心",
    "搞定",
    "棒",
    "不错",
    "厉害",
    "哈哈",
    "yay",
    "great",
    "awesome",
    "done",
    "success",
)
_PLAYFUL_KEYWORDS = (
    "嘿嘿",
    "笑死",
    "rua",
    "陪我",
    "抱抱",
    "亲亲",
    "想你",
    "无聊",
    "逗我",
    "玩",
)
_FOCUSED_KEYWORDS = (
    "帮我",
    "看一下",
    "看看",
    "检查",
    "分析",
    "搜索",
    "读取",
    "执行",
    "修",
    "改",
    "日志",
    "文件",
    "代码",
    "命令",
    "read",
    "check",
    "search",
    "fix",
    "run",
    "error",
)
_RELATIONSHIP_KEYWORDS = (
    "在吗",
    "陪陪我",
    "陪着我",
    "别走",
    "晚安",
    "早安",
    "辛苦啦",
    "辛苦了",
    "谢谢你",
    "想跟你聊聊",
    "stay with me",
    "miss you",
    "thank you",
    "thanks",
)
_DIRECT_HELP_KEYWORDS = (
    "帮我",
    "麻烦你",
    "拜托",
    "可以吗",
    "看看",
    "看一下",
    "处理一下",
    "查一下",
    "please",
)

_SUPPORT_NEED_TO_MODE = {
    "comfort": "comfort",
    "encourage": "encourage",
    "focused": "focused",
    "quiet_company": "quiet_company",
    "celebrate": "encourage",
}


@dataclass(frozen=True, slots=True)
class TurnSignals:
    comfort: float
    encourage: float
    playful: float
    focused: float
    relationship: float
    direct_help: bool
    question_count: int
    exclaim_count: int
    short_user: bool


def build_companion_intent(
    *,
    user_text: str,
    kernel_output: str,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> CompanionIntent:
    """Infer a lightweight companionship strategy for the current turn."""

    signals = _collect_turn_signals(user_text=user_text, kernel_output=kernel_output)
    mode = _pick_mode(signals, affect_state=affect_state, emotion_signal=emotion_signal)
    return CompanionIntent(
        mode=mode,
        warmth=_score_warmth(mode, signals, affect_state=affect_state, emotion_signal=emotion_signal),
        initiative=_score_initiative(mode, signals, affect_state=affect_state, emotion_signal=emotion_signal),
        intensity=_score_intensity(mode, signals, affect_state=affect_state, emotion_signal=emotion_signal),
    )


def _collect_turn_signals(*, user_text: str, kernel_output: str) -> TurnSignals:
    user = str(user_text or "").strip().lower()
    kernel = str(kernel_output or "").strip().lower()

    return TurnSignals(
        comfort=_keyword_score(user, kernel, _COMFORT_KEYWORDS, user_weight=1.15, kernel_weight=0.25),
        encourage=_keyword_score(user, kernel, _ENCOURAGE_KEYWORDS, user_weight=1.10, kernel_weight=0.25),
        playful=_keyword_score(user, kernel, _PLAYFUL_KEYWORDS, user_weight=1.20, kernel_weight=0.15),
        focused=_keyword_score(user, kernel, _FOCUSED_KEYWORDS, user_weight=1.10, kernel_weight=0.45),
        relationship=_keyword_score(user, kernel, _RELATIONSHIP_KEYWORDS, user_weight=1.20, kernel_weight=0.10),
        direct_help=_contains_any(user, _DIRECT_HELP_KEYWORDS),
        question_count=user.count("?") + user.count("？"),
        exclaim_count=user.count("!") + user.count("！"),
        short_user=len(user) <= 14,
    )


def _pick_mode(
    signals: TurnSignals,
    *,
    affect_state: "AffectState | None",
    emotion_signal: "EmotionSignal | None",
) -> str:
    if affect_state is not None:
        if (
            affect_state.pressure >= 0.42
            and signals.focused < 1.2
            and signals.encourage < 1.2
            and signals.playful < 1.2
        ):
            return "comfort"
        if affect_state.vitality <= 0.28 and signals.playful >= 1.0:
            return "quiet_company"
    semantic_mode = _pick_mode_from_emotion_signal(emotion_signal)
    if semantic_mode:
        return semantic_mode
    if signals.comfort >= 1.0:
        return "comfort"
    if signals.encourage >= 1.0 and signals.encourage >= signals.focused + 0.15:
        return "encourage"
    if signals.focused >= 1.0 and signals.focused >= signals.playful + 0.30:
        return "focused"
    if signals.playful >= 1.0:
        return "playful"
    if signals.relationship >= 1.0 or signals.short_user:
        if affect_state is not None and affect_state.current_pad.pleasure <= -0.24:
            return "comfort"
        return "quiet_company"
    if signals.direct_help:
        return "focused"
    return "quiet_company"


def _score_warmth(
    mode: str,
    signals: TurnSignals,
    *,
    affect_state: "AffectState | None",
    emotion_signal: "EmotionSignal | None",
) -> float:
    base = {
        "comfort": 0.94,
        "encourage": 0.90,
        "playful": 0.88,
        "focused": 0.82,
        "quiet_company": 0.87,
    }[mode]
    value = base
    value += 0.03 * min(signals.relationship, 2.0)
    value += 0.02 * min(signals.comfort, 2.0)
    if mode == "focused" and signals.direct_help:
        value += 0.03
    if mode == "quiet_company" and signals.short_user:
        value += 0.02
    if affect_state is not None:
        value += 0.08 * max(0.0, affect_state.pressure)
        value += 0.06 * max(0.0, -affect_state.current_pad.pleasure)
        if affect_state.vitality <= 0.35:
            value += 0.03
    if emotion_signal is not None:
        if emotion_signal.support_need == "comfort":
            value += 0.05
        elif emotion_signal.support_need == "quiet_company":
            value += 0.03
        elif emotion_signal.support_need in {"encourage", "celebrate"}:
            value += 0.02
        if emotion_signal.primary_emotion in {"sad", "hurt", "lonely", "overwhelmed"}:
            value += 0.03
    return _clamp(value)


def _score_initiative(
    mode: str,
    signals: TurnSignals,
    *,
    affect_state: "AffectState | None",
    emotion_signal: "EmotionSignal | None",
) -> float:
    base = {
        "comfort": 0.36,
        "encourage": 0.62,
        "playful": 0.70,
        "focused": 0.56,
        "quiet_company": 0.40,
    }[mode]
    value = base
    if signals.direct_help:
        value += 0.06
    value += 0.03 * min(signals.question_count, 2)
    if mode == "comfort" and signals.direct_help:
        value += 0.08
    if mode == "quiet_company" and signals.relationship >= 1.0:
        value += 0.04
    if affect_state is not None:
        value += 0.10 * max(0.0, affect_state.current_pad.dominance)
        value -= 0.10 * max(0.0, -affect_state.current_pad.dominance)
        if affect_state.vitality <= 0.35:
            value -= 0.05
    if emotion_signal is not None:
        if emotion_signal.support_need == "focused":
            value += 0.08
        elif emotion_signal.support_need == "comfort":
            value -= 0.04
            if emotion_signal.wants_action:
                value += 0.04
        elif emotion_signal.support_need == "quiet_company":
            value -= 0.06
        elif emotion_signal.support_need in {"encourage", "celebrate"}:
            value += 0.03
    return _clamp(value)


def _score_intensity(
    mode: str,
    signals: TurnSignals,
    *,
    affect_state: "AffectState | None",
    emotion_signal: "EmotionSignal | None",
) -> float:
    base = {
        "comfort": 0.30,
        "encourage": 0.58,
        "playful": 0.60,
        "focused": 0.38,
        "quiet_company": 0.24,
    }[mode]
    value = base
    value += 0.04 * min(signals.exclaim_count, 2)
    if mode == "comfort" and signals.direct_help:
        value += 0.04
    if mode == "focused" and signals.focused >= 2.0:
        value += 0.04
    if mode == "encourage" and signals.encourage >= 2.0:
        value += 0.03
    if mode == "quiet_company" and signals.short_user:
        value -= 0.03
    if affect_state is not None:
        value += 0.12 * max(0.0, affect_state.current_pad.arousal)
        if affect_state.vitality <= 0.35:
            value -= 0.08
        if affect_state.pressure >= 0.38 and mode in {"comfort", "focused", "quiet_company"}:
            value -= 0.04
    if emotion_signal is not None:
        value += 0.10 * max(0.0, emotion_signal.intensity - 0.35)
        if emotion_signal.support_need == "quiet_company":
            value -= 0.05
        elif emotion_signal.support_need in {"encourage", "celebrate"}:
            value += 0.05
        elif emotion_signal.support_need == "comfort":
            value -= 0.02
    return _clamp(value)


def _pick_mode_from_emotion_signal(emotion_signal: "EmotionSignal | None") -> str:
    if emotion_signal is None:
        return ""

    support_need = str(emotion_signal.support_need or "").strip()
    mapped_mode = _SUPPORT_NEED_TO_MODE.get(support_need, "")
    if not mapped_mode:
        return ""

    confidence = float(emotion_signal.confidence or 0.0)
    intensity = float(emotion_signal.intensity or 0.0)
    if confidence < 0.45 and intensity < 0.55:
        return ""
    return mapped_mode


def _keyword_score(
    user_text: str,
    kernel_output: str,
    keywords: tuple[str, ...],
    *,
    user_weight: float,
    kernel_weight: float,
) -> float:
    user_hits = _count_hits(user_text, keywords)
    kernel_hits = _count_hits(kernel_output, keywords)
    return user_hits * user_weight + kernel_hits * kernel_weight


def _count_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _clamp(value: float, minimum: float = 0.12, maximum: float = 0.98) -> float:
    return max(minimum, min(maximum, value))
