"""Minimal semantic emotion inference for one user turn."""

from __future__ import annotations

from emoticorebot.affect.models import AffectState, EmotionSignal

_EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "happy": (
        "开心",
        "高兴",
        "喜欢",
        "太好了",
        "棒",
        "舒服",
        "温暖",
        "happy",
        "glad",
        "nice",
    ),
    "excited": (
        "激动",
        "兴奋",
        "冲",
        "爽",
        "太棒了",
        "哈哈",
        "yay",
        "awesome",
        "excited",
        "lets go",
    ),
    "sad": (
        "难过",
        "低落",
        "想哭",
        "沮丧",
        "失落",
        "心情不好",
        "sad",
        "down",
        "upset",
    ),
    "hurt": (
        "委屈",
        "受伤",
        "心寒",
        "心碎",
        "扎心",
        "被忽视",
        "不被理解",
        "hurt",
        "heartbroken",
    ),
    "anxious": (
        "焦虑",
        "紧张",
        "担心",
        "害怕",
        "不安",
        "急",
        "慌",
        "panic",
        "anxious",
        "nervous",
        "worried",
    ),
    "frustrated": (
        "烦",
        "崩溃",
        "无语",
        "麻了",
        "卡住",
        "出错",
        "报错",
        "失败",
        "不行",
        "frustrated",
        "annoyed",
        "stuck",
        "error",
    ),
    "lonely": (
        "孤单",
        "寂寞",
        "一个人",
        "陪陪我",
        "陪着我",
        "别走",
        "在吗",
        "想你",
        "想聊聊",
        "lonely",
        "alone",
        "stay with me",
    ),
    "overwhelmed": (
        "累",
        "压力",
        "扛不住",
        "撑不住",
        "受不了",
        "好多事",
        "太多了",
        "overwhelmed",
        "exhausted",
        "burnt out",
        "tired",
    ),
}

_ACTION_KEYWORDS = (
    "帮我",
    "看一下",
    "看看",
    "处理一下",
    "查一下",
    "修一下",
    "改一下",
    "read ",
    "check ",
    "fix ",
    "run ",
    "please",
)

_TASKISH_KEYWORDS = (
    "文件",
    "代码",
    "日志",
    "命令",
    "报错",
    "错误",
    "bug",
    "issue",
)


def infer_emotion_signal(*, user_text: str, affect_state: AffectState | None = None) -> EmotionSignal:
    """Infer a minimal semantic emotion signal from the user text and PAD state."""

    text = str(user_text or "").strip().lower()
    wants_action = _contains_any(text, _ACTION_KEYWORDS) or _contains_any(text, _TASKISH_KEYWORDS)
    scores, triggers = _score_emotions(text)

    primary_emotion = _pick_primary_emotion(scores=scores, text=text, affect_state=affect_state)
    trigger_text = triggers.get(primary_emotion, "")
    max_score = float(scores.get(primary_emotion, 0.0) or 0.0)
    second_score = _second_best_score(scores=scores, winner=primary_emotion)
    intensity = _compute_intensity(
        primary_emotion=primary_emotion,
        max_score=max_score,
        wants_action=wants_action,
        affect_state=affect_state,
    )
    confidence = _compute_confidence(
        primary_emotion=primary_emotion,
        max_score=max_score,
        second_score=second_score,
        has_trigger=bool(trigger_text),
        affect_state=affect_state,
    )
    support_need = _pick_support_need(
        primary_emotion=primary_emotion,
        wants_action=wants_action,
        affect_state=affect_state,
    )

    return EmotionSignal(
        primary_emotion=primary_emotion,
        intensity=intensity,
        confidence=confidence,
        support_need=support_need,
        wants_action=wants_action,
        trigger_text=trigger_text,
    )


def _score_emotions(text: str) -> tuple[dict[str, float], dict[str, str]]:
    scores = {emotion: 0.0 for emotion in _EMOTION_KEYWORDS}
    triggers: dict[str, str] = {}
    if not text:
        return scores, triggers

    exclaim_count = min(3, text.count("!") + text.count("！"))
    question_count = min(2, text.count("?") + text.count("？"))

    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                scores[emotion] += 1.0
                triggers.setdefault(emotion, keyword)

    scores["excited"] += 0.18 * exclaim_count
    scores["anxious"] += 0.12 * question_count
    if "为什么" in text or "咋" in text:
        scores["frustrated"] += 0.18 * question_count
    return scores, triggers


def _pick_primary_emotion(
    *,
    scores: dict[str, float],
    text: str,
    affect_state: AffectState | None,
) -> str:
    if scores:
        winner, winner_score = max(scores.items(), key=lambda item: item[1])
        if winner_score > 0:
            return winner

    if affect_state is None:
        return "neutral"

    pleasure = affect_state.current_pad.pleasure
    arousal = affect_state.current_pad.arousal
    pressure = affect_state.pressure
    vitality = affect_state.vitality

    if pressure >= 0.48 and vitality <= 0.40:
        return "overwhelmed"
    if pleasure <= -0.24 and arousal >= 0.22:
        if "错" in text or "bug" in text or "卡" in text:
            return "frustrated"
        return "anxious"
    if pleasure <= -0.28 and arousal <= 0.05:
        return "sad"
    if pleasure >= 0.30 and arousal >= 0.28:
        return "excited"
    if pleasure >= 0.16:
        return "happy"
    return "neutral"


def _pick_support_need(
    *,
    primary_emotion: str,
    wants_action: bool,
    affect_state: AffectState | None,
) -> str:
    if primary_emotion in {"happy", "excited"}:
        return "encourage" if wants_action else "celebrate"
    if primary_emotion in {"sad", "hurt"}:
        return "comfort"
    if primary_emotion == "lonely":
        return "quiet_company"
    if primary_emotion in {"anxious", "frustrated"}:
        return "focused" if wants_action else "comfort"
    if primary_emotion == "overwhelmed":
        if wants_action and affect_state is not None and affect_state.pressure < 0.65:
            return "focused"
        return "comfort"
    return "focused" if wants_action else "quiet_company"


def _compute_intensity(
    *,
    primary_emotion: str,
    max_score: float,
    wants_action: bool,
    affect_state: AffectState | None,
) -> float:
    intensity = 0.12 + (0.20 * max_score)
    if affect_state is not None:
        intensity += 0.18 * max(0.0, affect_state.pressure)
        intensity += 0.12 * max(0.0, -affect_state.current_pad.pleasure)
        intensity += 0.08 * max(0.0, affect_state.current_pad.arousal)
        intensity += 0.06 * max(0.0, 0.38 - affect_state.vitality)
    if wants_action and primary_emotion in {"anxious", "frustrated", "overwhelmed"}:
        intensity += 0.08
    return _clamp(intensity, 0.0, 1.0)


def _compute_confidence(
    *,
    primary_emotion: str,
    max_score: float,
    second_score: float,
    has_trigger: bool,
    affect_state: AffectState | None,
) -> float:
    confidence = 0.28 + (0.18 * max_score)
    confidence += 0.10 * max(0.0, max_score - second_score)
    if has_trigger:
        confidence += 0.08
    if max_score <= 0.0 and primary_emotion != "neutral" and affect_state is not None:
        confidence += 0.06
    return _clamp(confidence, 0.0, 1.0)


def _second_best_score(*, scores: dict[str, float], winner: str) -> float:
    values = [value for emotion, value in scores.items() if emotion != winner]
    if not values:
        return 0.0
    return max(values)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))
