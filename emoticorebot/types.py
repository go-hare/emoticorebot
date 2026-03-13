"""Shared protocol types used across brain, task, runtime, and reflection."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

BrainFinalDecision = Literal["", "answer", "ask_user", "continue"]  # 主脑本轮最终控制决策
BrainTaskAction = Literal["", "none", "create_task", "fill_task"]  # 主脑对任务系统的动作

TaskLifecycleState = Literal[
    "created",
    "running",
    "waiting_input",
    "blocked_input",
    "done",
    "failed",
    "cancelled",
]  # 任务在运行时中的生命周期状态
TaskControlState = Literal["running", "waiting_input", "completed", "failed"]  # central/任务控制态
TaskEventType = Literal["created", "started", "progress", "need_input", "done", "failed"]  # 任务事件类型
TaskResultStatus = Literal["success", "partial", "pending", "failed"]  # 任务结果态

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

ReviewSeverity = Literal["low", "medium", "high"]  # 审核严重程度

MemoryAudience = Literal["brain", "task", "shared"]  # 记忆面向的消费方
MemoryKind = Literal["episodic", "durable", "procedural"]  # 记忆的稳定性/形态
MemoryType = Literal[
    "insight",
    "user",
    "preference",
    "workflow",
    "skill",
    "turn_insight",
]


class ReviewItem(TypedDict, total=False):
    """待审核项。"""

    item_id: str  # 审核项唯一 ID
    label: str  # 审核项标题/名称
    reason: str  # 为什么需要审核
    severity: ReviewSeverity  # 风险等级
    blocking: bool  # 是否阻塞任务完成
    required_action: str  # 需要采取的动作
    evidence: list[str]  # 相关证据或引用
    payload: dict[str, Any]  # 扩展结构化信息


class TraceItem(TypedDict, total=False):
    """任务执行追踪中的单条记录。"""

    timestamp: str  # 事件时间
    role: str  # 记录角色，如 assistant/tool
    type: str  # 事件粗分类，如 tool_call/tool_result
    event: str  # 更细粒度的事件名
    phase: str  # 所处阶段
    node: str  # graph 节点名
    name: str  # 通用名称字段
    tool: str  # 工具名（兼容字段）
    tool_name: str  # 工具名（标准字段）
    tool_call_id: str  # 工具调用 ID
    content: str | list[dict[str, Any]]  # 简化内容或消息块
    result: Any  # 执行结果原文/摘要
    is_error: bool  # 是否错误记录
    error: str  # 错误说明
    args: dict[str, Any]  # 调用参数
    payload: dict[str, Any]  # 扩展负载
    namespace: list[str]  # 流式执行命名空间
    stream_mode: str  # 流模式，如 updates/messages
    trace_signature: str  # 去重或追踪签名


class TaskInputRequest(TypedDict, total=False):
    """任务向用户追问时的输入请求。"""

    field: str  # 缺失字段名
    question: str  # 给用户展示的问题


class TaskSpec(TypedDict, total=False):
    """主脑委托给任务系统/central 的结构化任务描述。"""

    task_id: str  # 任务唯一 ID
    origin_message_id: str  # 触发该任务的原始消息 ID
    title: str  # 任务标题，便于展示/查询
    request: str  # 原始任务请求
    goal: str  # 任务最终目标
    constraints: list[str]  # 任务约束条件
    success_criteria: list[str]  # 完成判定标准
    expected_output: str  # 期望输出形式
    history: list[dict[str, Any]]  # 传给任务的会话历史
    task_context: dict[str, Any]  # 额外任务上下文
    history_context: str  # 从历史中提炼出的摘要上下文
    memory_bundle_ids: list[str]  # 主脑传入的记忆包 ID
    skill_hints: list[str]  # 主脑传入的技能提示
    media: list[str]  # 附件/图片路径
    channel: str  # 来源渠道
    chat_id: str  # 会话对象 ID
    session_id: str  # 会话 ID


class TaskState(TypedDict, total=False):
    """运行时任务快照。"""

    invoked: bool  # 当前轮是否实际触发过任务
    task_id: str  # 任务 ID
    title: str  # 任务标题
    params: TaskSpec  # 任务规格快照
    status: TaskLifecycleState  # 生命周期状态
    result_status: TaskResultStatus  # 结果状态：success/partial/pending/failed
    control_state: TaskControlState  # 执行控制状态
    summary: str  # 当前摘要或最终摘要
    analysis: str  # 内部分析说明
    error: str  # 错误原因
    missing: list[str]  # 缺失输入字段
    input_request: TaskInputRequest  # 当前追问请求
    stage_info: str  # 进度描述
    pending_review: list[ReviewItem]  # 待审核项
    recommended_action: str  # 建议下一步
    confidence: float  # 结果置信度
    attempt_count: int  # 执行尝试次数
    task_trace: list[TraceItem]  # 执行追踪


class TaskEvent(TypedDict, total=False):
    """任务系统发回 runtime 的事件包。"""

    task_id: str  # 任务 ID
    channel: str  # 路由渠道
    chat_id: str  # 路由会话 ID
    message_id: str  # 对应消息 ID
    type: TaskEventType  # 事件类型
    title: str  # 任务标题
    params: TaskSpec  # 任务规格快照
    message: str  # 事件消息/兼容字段
    summary: str  # 事件摘要
    reason: str  # 失败原因
    field: str  # 缺失字段
    question: str  # 追问内容
    control_state: TaskControlState  # 控制状态
    result_status: TaskResultStatus  # 结果状态
    analysis: str  # 分析说明
    missing: list[str]  # 缺失输入列表
    pending_review: list[ReviewItem]  # 待审核项
    recommended_action: str  # 建议下一步
    confidence: float  # 结果置信度
    attempt_count: int  # 尝试次数
    task_trace: list[TraceItem]  # 执行追踪
    payload: dict[str, Any]  # 扩展事件负载


class TaskExecutionResult(TypedDict, total=False):
    """task/central 执行结束后返回的结构化结果。"""

    control_state: TaskControlState  # 任务最终控制态：完成/等待补充/失败
    status: TaskResultStatus  # 任务结果状态：success/partial/pending/failed
    analysis: str  # 对执行过程的紧凑分析，不展开原始推理
    message: str  # 任务最终产出的正文结果
    missing: list[str]  # 仍缺失的输入字段列表
    pending_review: list[ReviewItem]  # 需要主脑或用户进一步审核的项
    recommended_action: str  # 建议下一步动作或追问
    confidence: float  # 当前结果置信度
    attempt_count: int  # 执行尝试次数
    task_trace: list[TraceItem]  # 执行轨迹（通常由运行时补充）


class BrainControlPacket(TypedDict, total=False):
    """主脑本轮输出的结构化控制包。"""

    task_action: BrainTaskAction  # 主脑要求的任务动作
    task_reason: str  # 为什么采取该动作
    final_decision: BrainFinalDecision  # 本轮最终控制决策
    final_message: str  # 给用户的最终回复
    task_brief: str  # 给任务系统的简要说明
    task: TaskSpec  # 完整任务描述
    intent: str  # 对用户意图的判断
    working_hypothesis: str  # 当前工作假设
    notify_user: bool  # 是否需要立刻通知用户
    execution_summary: str  # 本轮执行摘要
    retrieval_query: str  # 记忆检索查询
    retrieval_focus: list[str]  # 检索关注点
    retrieved_memory_ids: list[str]  # 命中的记忆 ID
    message_id: str  # 当前轮消息 ID
    model_name: str  # 使用的模型名
    prompt_tokens: int  # 输入 token 数
    completion_tokens: int  # 输出 token 数
    total_tokens: int  # 总 token 数


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
    """对实时状态的增量更新建议。"""

    should_apply: bool  # 是否建议应用
    confidence: float  # 应用该更新的置信度
    reason: str  # 更新原因
    pad_delta: dict[str, float]  # PAD 增量
    drives_delta: dict[str, float]  # drives 增量


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
    state_update: StateUpdateDelta  # 状态增量更新建议
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
