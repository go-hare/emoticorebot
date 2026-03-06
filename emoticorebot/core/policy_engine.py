"""Policy engine - 策略引擎。

根据 TurnSignals 计算 FusionPolicy（empathy_depth / fact_depth / tool_budget / tone）。
不依赖外部 LLM，仅做纯数值计算，单轮无状态。
"""

from __future__ import annotations

from dataclasses import dataclass

from emoticorebot.core.signal_extractor import TurnSignals


@dataclass(frozen=True)
class FusionPolicy:
    """融合策略参数（一次请求的完整决策配置）。"""
    iq_weight: float           # IQ 权重 0-1（保留用于分析）
    eq_weight: float           # EQ 权重 0-1（保留用于分析）
    empathy_depth: int         # 共情深度 0-2
    fact_depth: int            # 事实深度 1-3
    tool_budget: int           # 工具调用次数上限
    tone: str                  # 输出风格 "professional|warm|balanced|concise"
    confidence: float          # 策略置信度 0-1


_DEFAULT_FUSION_CONFIG = {
    "weights": {
        "emotion_intensity": 0.45,
        "relationship_need": 0.35,
        "inverse_task_strength": 0.20,
    },
    "thresholds": {
        "high_iq_weight": 0.7,
        "high_eq_weight": 0.7,
    },
    "tool_budget": {
        "high_iq": 6,
        "high_eq": 3,
        "balanced": 4,
    },
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class PolicyEngine:
    """将信号转换为策略参数。"""

    def __init__(self, fusion_config: dict | None = None) -> None:
        self._cfg = _DEFAULT_FUSION_CONFIG.copy()
        if isinstance(fusion_config, dict):
            self._deep_update(self._cfg, fusion_config)

    @staticmethod
    def _deep_update(base: dict, override: dict) -> None:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                PolicyEngine._deep_update(base[key], value)
            else:
                base[key] = value

    def make_policy(
        self,
        signals: TurnSignals,
        runtime_adjustment: dict | None = None,
    ) -> FusionPolicy:
        weights = self._cfg["weights"]
        th = self._cfg["thresholds"]
        tool_budget_cfg = self._cfg["tool_budget"]

        eq_weight = _clamp01(
            float(weights["emotion_intensity"]) * signals.emotion_intensity
            + float(weights["relationship_need"]) * signals.relationship_need
            + float(weights["inverse_task_strength"]) * (1.0 - signals.task_strength)
        )

        if signals.safety_risk >= 0.8:
            eq_weight = max(eq_weight, 0.8)

        iq_weight = 1.0 - eq_weight

        if isinstance(runtime_adjustment, dict):
            eq_bias = float(runtime_adjustment.get("eq_bias", 0.0))
            iq_bias = float(runtime_adjustment.get("iq_bias", 0.0))
            eq_weight = _clamp01(eq_weight + eq_bias - iq_bias)
            iq_weight = 1.0 - eq_weight

        if iq_weight > float(th["high_iq_weight"]):
            tone = "professional"
            fact_depth = 2
            empathy_depth = 0
            tool_budget = int(tool_budget_cfg["high_iq"])
        elif eq_weight > float(th["high_eq_weight"]):
            tone = "warm"
            fact_depth = 1
            empathy_depth = 2
            tool_budget = int(tool_budget_cfg["high_eq"])
        else:
            tone = "balanced"
            fact_depth = 2
            empathy_depth = 1
            tool_budget = int(tool_budget_cfg["balanced"])

        if isinstance(runtime_adjustment, dict):
            tone_pref = runtime_adjustment.get("tone_preference")
            allowed_tones = {"warm", "professional", "balanced", "concise"}
            if isinstance(tone_pref, str) and tone_pref.strip() in allowed_tones:
                tone = tone_pref.strip()
            delta = int(runtime_adjustment.get("tool_budget_delta", 0))
            tool_budget = max(1, tool_budget + delta)

        confidence = _clamp01(0.35 + abs(iq_weight - eq_weight))

        reasoning_parts = []
        if iq_weight > float(th["high_iq_weight"]):
            reasoning_parts.append(f"high_iq({iq_weight:.2f})")
        elif eq_weight > float(th["high_eq_weight"]):
            reasoning_parts.append(f"high_eq({eq_weight:.2f})")
        else:
            reasoning_parts.append(f"balanced(iq={iq_weight:.2f},eq={eq_weight:.2f})")
        if signals.emotion_intensity > 0.6:
            reasoning_parts.append(f"emotion_strong({signals.emotion_intensity:.2f})")
        if signals.task_strength > 0.6:
            reasoning_parts.append(f"task_strong({signals.task_strength:.2f})")
        if signals.safety_risk >= 0.8:
            reasoning_parts.append(f"safety_risk({signals.safety_risk:.2f})")
        if isinstance(runtime_adjustment, dict):
            adj_parts = []
            if runtime_adjustment.get("eq_bias"):
                adj_parts.append(f"eq_bias{runtime_adjustment['eq_bias']:+.2f}")
            if runtime_adjustment.get("iq_bias"):
                adj_parts.append(f"iq_bias{runtime_adjustment['iq_bias']:+.2f}")
            if runtime_adjustment.get("tone_preference"):
                adj_parts.append(f"tone={runtime_adjustment['tone_preference']}")
            if adj_parts:
                reasoning_parts.append(f"adjusted({','.join(adj_parts)})")

        from loguru import logger
        logger.debug(
            "Policy: {} → tone={} empathy={} fact={} budget={}",
            " | ".join(reasoning_parts) or "default",
            tone, empathy_depth, fact_depth, tool_budget,
        )

        return FusionPolicy(
            iq_weight=iq_weight,
            eq_weight=eq_weight,
            empathy_depth=empathy_depth,
            fact_depth=fact_depth,
            tool_budget=tool_budget,
            tone=tone,
            confidence=confidence,
        )
