"""Signal extractor for fusion architecture.

从用户输入中提取任务强度、情绪强度、紧急度等信号，供策略引擎决策。
迁移自 core/signal_extractor.py，适配 fusion 双向协同架构。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnSignals:
    """单轮对话信号"""
    task_strength: float        # 任务显著度 0-1
    emotion_intensity: float    # 情绪强度 0-1
    relationship_need: float    # 关系维护需求 0-1
    urgency: float              # 紧急度 0-1
    safety_risk: float          # 安全风险 0-1
    reason: str = ""            # 提取理由（调试用）


_TASK_HINTS = (
    "查", "查询", "帮我", "帮忙", "设置", "打开", "订", "写", "翻译", "总结", "执行", "run", "fix"
)
_EMOTION_HINTS = (
    "难过", "崩溃", "好烦", "烦死了", "气死了", "失恋", "焦虑", "抑郁", "绝望", "委屈", "开心", "谢谢"
)
_URGENCY_HINTS = ("马上", "立刻", "尽快", "紧急", "asap", "urgent")


def _clamp01(value: float) -> float:
    """限制在 0-1 范围"""
    return max(0.0, min(1.0, value))


class SignalExtractor:
    """从用户输入提取多维信号"""

    def extract(self, user_input: str, emotion_state: str = "") -> TurnSignals:
        """
        提取单轮信号。
        
        :param user_input: 用户输入文本
        :param emotion_state: 当前情绪状态标签（如"焦虑"）
        :return: TurnSignals
        """
        text = user_input.strip().lower()
        
        # 关键词匹配
        task_hits = sum(1 for w in _TASK_HINTS if w in text)
        emotion_hits = sum(1 for w in _EMOTION_HINTS if w in text)
        urgency_hits = sum(1 for w in _URGENCY_HINTS if w in text)

        # 轻量级文本特征
        question_mark = 1 if "?" in text or "？" in text else 0
        long_text_bonus = 0.15 if len(text) > 80 else 0.0
        exclamation = len(re.findall(r"[!！]", text))

        # 计算信号强度
        task_strength = _clamp01(0.25 * task_hits + 0.15 * question_mark + long_text_bonus)
        emotion_intensity = _clamp01(0.22 * emotion_hits + 0.08 * min(exclamation, 3))
        relationship_need = _clamp01(0.6 * emotion_intensity + (0.1 if "你" in text else 0.0))
        urgency = _clamp01(0.35 * urgency_hits + 0.1 * question_mark)
        safety_risk = 0.0

        # 安全风险检测
        if any(x in text for x in ("自杀", "伤害自己", "kill myself")):
            safety_risk = 1.0

        reason = f"task_hits={task_hits}, emotion_hits={emotion_hits}, urgency_hits={urgency_hits}"
        
        # 根据当前情绪状态微调
        if "焦虑" in emotion_state or "悲" in emotion_state:
            emotion_intensity = _clamp01(emotion_intensity + 0.08)
            relationship_need = _clamp01(relationship_need + 0.05)

        return TurnSignals(
            task_strength=task_strength,
            emotion_intensity=emotion_intensity,
            relationship_need=relationship_need,
            urgency=urgency,
            safety_risk=safety_risk,
            reason=reason,
        )
