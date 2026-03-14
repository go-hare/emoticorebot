"""Shared non-runtime types plus protocol re-exports used across the app."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from emoticorebot.brain.decision_packet import BrainControlPacket, BrainFinalDecision, BrainTaskAction
from emoticorebot.protocol.events import TaskEvent, TaskEventType
from emoticorebot.protocol.task_models import (
    ReviewItem,
    ReviewSeverity,
    TaskControlState,
    TaskInputRequest,
    TaskLifecycleState,
    TaskResultStatus,
    TaskSpec,
    TaskState,
    TraceItem,
)
from emoticorebot.protocol.task_result import TaskExecutionResult

ReflectionSourceType = Literal["user_turn", "task_event", "internal_task_event"]  # 反思输入来源
ExecutionStatus = Literal[
    "none",
    "done",
    "need_more",
    "failed",
    "waiting_input",
    "running",
    "partial",
    "completed",
]
ExecutionEffectiveness = Literal["high", "medium", "low", "none"]  # 执行有效性评级

MemoryAudience = Literal["brain", "task", "shared"]  # 记忆面向的消费方
MemoryKind = Literal["episodic", "durable", "procedural"]  # 记忆的稳定性/形态
MemoryType = Literal["insight", "user", "preference", "workflow", "skill", "turn_insight"]


class EmotionState(TypedDict):
    """情绪快照。"""

    emotion_label: str  # 情绪标签
    pad: dict[str, float]  # PAD 三维情绪值
    drives: dict[str, float]  # 驱动力数值


class ExecutionInfo(TypedDict, total=False):
    """反思层使用的标准化执行信息。"""

    invoked: bool  # 本轮是否发生执行
    status: ExecutionStatus  # 执行状态
    summary: str  # 一句话执行摘要
    confidence: float  # 执行置信度
    attempt_count: int  # 尝试次数
    missing: list[str]  # 缺失输入
    failure_reason: str  # 失败原因
    recommended_action: str  # 建议下一步


class ReflectionInput(TypedDict, total=False):
    """逐轮反思的标准输入包。"""

    turn_id: str  # 当前轮 ID
    message_id: str  # 当前消息 ID
    session_id: str  # 会话 ID
    source_type: ReflectionSourceType  # 来源类型
    user_input: str  # 输入给反思的“用户侧”文本
    output: str  # 当前输出文本
    assistant_output: str  # 助手最终输出文本
    channel: str  # 渠道
    chat_id: str  # 聊天对象
    emotion: EmotionState  # 情绪快照
    brain: BrainControlPacket  # 主脑决策包
    execution: ExecutionInfo | None  # 标准化执行信息
    task: TaskState  # 任务快照
    task_trace: list[TraceItem]  # 执行追踪
    metadata: dict[str, Any]  # 扩展元数据


class StateUpdateDelta(TypedDict, total=False):
    """主脑对本轮状态记录结果的判断。字段名保留 delta，但值表示判断后的状态值。"""

    should_apply: bool  # 是否建议同步到实时状态
    confidence: float  # 应用该更新的置信度
    reason: str  # 更新原因
    pad_delta: dict[str, float]  # PAD 状态值（字段名保留 delta）
    drives_delta: dict[str, float]  # drives 状态值（字段名保留 delta）


class MemoryCandidate(TypedDict, total=False):
    """可写入长期记忆的候选项。"""

    audience: MemoryAudience  # 面向 brain/task/shared 的记忆受众
    kind: MemoryKind  # 记忆形态
    type: MemoryType  # 记忆具体类型
    summary: str  # 一句话摘要
    content: str  # 记忆正文
    importance: int  # 重要度 1-10
    confidence: float  # 置信度
    stability: float  # 稳定度
    tags: list[str]  # 标签列表
    payload: dict[str, Any]  # 扩展结构化负载


class ExecutionReview(TypedDict, total=False):
    """对执行过程的紧凑评价。"""

    attempt_count: int  # 执行尝试次数
    effectiveness: ExecutionEffectiveness  # 执行有效性
    main_failure_reason: str  # 主要失败原因
    missing_inputs: list[str]  # 缺失输入
    next_execution_hint: str  # 下次执行提示


class TurnReflectionOutput(TypedDict, total=False):
    """逐轮反思输出。"""

    summary: str  # 本轮核心总结
    problems: list[str]  # 本轮问题列表
    resolution: str  # 问题如何解决
    outcome: Literal["success", "partial", "failed", "no_execution"]  # 本轮结果
    next_hint: str  # 下一轮承接提示
    user_updates: list[str]  # 用户画像更新候选
    soul_updates: list[str]  # 主脑风格更新候选
    state_update: StateUpdateDelta  # 状态记录判断
    memory_candidates: list[MemoryCandidate]  # 长期记忆候选
    execution_review: ExecutionReview  # 执行复盘


class SkillHint(TypedDict, total=False):
    """深反思产出的技能提示。"""

    summary: str  # 一句话概括
    content: str  # 更完整说明
    trigger: str  # 触发条件
    hint: str  # 给 task 的使用提示
    skill_name: str  # 技能名建议


class DeepReflectionOutput(TypedDict, total=False):
    """深反思输出。"""

    summary: str  # 这一阶段的高层总结
    memory_candidates: list[MemoryCandidate]  # 长期记忆候选
    user_updates: list[str]  # 用户画像更新
    soul_updates: list[str]  # 主脑风格更新
    skill_hints: list[SkillHint]  # 技能提示


__all__ = [
    "BrainControlPacket",
    "BrainFinalDecision",
    "BrainTaskAction",
    "DeepReflectionOutput",
    "EmotionState",
    "ExecutionEffectiveness",
    "ExecutionInfo",
    "ExecutionReview",
    "ExecutionStatus",
    "MemoryAudience",
    "MemoryCandidate",
    "MemoryKind",
    "MemoryType",
    "ReflectionInput",
    "ReflectionSourceType",
    "ReviewItem",
    "ReviewSeverity",
    "SkillHint",
    "StateUpdateDelta",
    "TaskControlState",
    "TaskExecutionResult",
    "TaskEvent",
    "TaskEventType",
    "TaskInputRequest",
    "TaskLifecycleState",
    "TaskResultStatus",
    "TaskSpec",
    "TaskState",
    "TraceItem",
    "TurnReflectionOutput",
]
