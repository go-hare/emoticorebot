"""Type definitions for reflection module."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


# ============================================================
# 输入参数：传给 reflect_turn() 的参数
# ============================================================

class EmotionState(TypedDict):
    """情绪状态快照"""
    emotion_label: str          # 情绪标签，如 "平静"
    pad: dict[str, float]       # PAD 值 {"pleasure": 0.5, "arousal": 0.3, "dominance": 0.6}
    drives: dict[str, float]    # 驱动力 {"social": 50, "energy": 70}


class TurnReflectionParams(TypedDict):
    """逐轮反思输入参数"""
    user_input: str                      # 用户说了什么
    output: str                          # 你回复了什么
    emotion: EmotionState                # 情绪状态（从 emotion_mgr.snapshot() 获取）
    execution: "ExecutionInfo | None"    # 执行信息（没有就传 None）


class ExecutionInfo(TypedDict, total=False):
    """执行信息 - 主脑填写指南"""
    
    # === 必填（如果有执行）===
    invoked: bool           # True=本轮执行了任务，False=没执行
    status: str             # "done"=完成 | "need_more"=需要更多信息 | "failed"=失败 | "none"=未执行
    summary: str            # 一句话描述执行了什么，如 "调用 search_web 搜索 Python 教程"
    
    # === 可选但推荐 ===
    confidence: float       # 执行结果的置信度 0.0-1.0，如 0.8
    attempt_count: int      # 尝试了几次，如 1
    
    # === 如果执行失败或需要更多信息 ===
    missing: list[str]             # 缺什么信息，如 ["用户的具体需求", "文件路径"]
    failure_reason: str            # 为什么失败，如 "API 返回 404"
    recommended_action: str        # 建议下一步做什么，如 "询问用户具体需求"


# ============================================================
# 输出结构：reflect_turn() 返回的反思结果
# ============================================================

class StateUpdateDelta(TypedDict, total=False):
    """状态增量更新"""
    should_apply: bool             # 是否应用增量
    confidence: float              # 置信度 (0.0 ~ 1.0)
    reason: str                    # 更新原因
    pad_delta: dict[str, float]    # PAD 增量 (-0.3 ~ 0.3)
    drives_delta: dict[str, float] # 驱动力增量 (-20 ~ 20)


class MemoryCandidate(TypedDict, total=False):
    """长期记忆候选"""
    audience: Literal["brain", "task", "shared"]  # 受众
    kind: Literal["episodic", "durable", "procedural"]  # 记忆类型
    type: Literal["insight", "user", "preference", "workflow", "skill"]  # 记忆具体类型
    summary: str                   # 一句话摘要
    content: str                   # 完整内容
    importance: int                # 重要性 (1-10)
    confidence: float              # 置信度 (0.0-1.0)
    stability: float               # 稳定性 (0.0-1.0)
    tags: list[str]                # 标签列表
    payload: dict[str, Any]        # 类型扩展字段


class ExecutionReview(TypedDict, total=False):
    """执行评价"""
    attempt_count: int             # 尝试次数
    effectiveness: Literal["high", "medium", "low", "none"]  # 执行有效性
    main_failure_reason: str       # 主要失败原因
    missing_inputs: list[str]      # 缺失的输入信息
    next_execution_hint: str       # 下次执行的提示


class TurnReflectionOutput(TypedDict, total=False):
    """逐轮反思输出结构"""
    summary: str                   # 本轮核心总结
    problems: list[str]            # 问题列表
    resolution: str                # 解决方案
    outcome: Literal["success", "partial", "failed", "no_execution"]  # 结果状态
    next_hint: str                 # 下一轮提示
    user_updates: list[str]        # 用户信息直写候选
    soul_updates: list[str]        # 主脑风格直写候选
    state_update: StateUpdateDelta # 状态增量更新
    memory_candidates: list[MemoryCandidate]  # 长期记忆候选
    execution_review: ExecutionReview  # 执行评价


# ============================================================
# 深度反思（任务级反思）参数
# ============================================================

class DeepReflectionParams(TypedDict):
    """深度反思输入参数"""
    
    reason: str         # 为什么要做深度反思（可选，默认为空）
    warm_limit: int     # 从最近多少条认知事件中提炼（可选，默认 15）
    # 注意：events 会自动从 cognitive_events.jsonl 中读取，不需要手动传


class SkillHint(TypedDict, total=False):
    """技能提示"""
    summary: str        # 一句话概括
    content: str        # 完整说明
    trigger: str        # 什么情况下触发
    hint: str           # 给 task 的提示
    skill_name: str     # 技能名称


class DeepReflectionOutput(TypedDict, total=False):
    """深度反思输出结构"""
    summary: str                          # 这一阶段的高层总结
    memory_candidates: list[MemoryCandidate]  # 长期记忆候选
    user_updates: list[str]               # 用户画像更新
    soul_updates: list[str]               # 主脑风格更新
    skill_hints: list[SkillHint]          # 技能提示


__all__ = [
    # 逐轮反思
    "TurnReflectionParams",
    "EmotionState",
    "ExecutionInfo",
    "StateUpdateDelta",
    "MemoryCandidate",
    "ExecutionReview",
    "TurnReflectionOutput",
    # 深度反思
    "DeepReflectionParams",
    "SkillHint",
    "DeepReflectionOutput",
]
