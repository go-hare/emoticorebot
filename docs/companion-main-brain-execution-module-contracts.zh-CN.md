# Companion Main Brain / Execution Runtime Module Contracts（目标版草案）

## 0. 正式术语与命名规范

这份目标版文档中的术语、字段和枚举统一按下面这套定义解释。

### 0.1 组件正式名

- `MainBrainFrontLoop`
  - 中文别名：主脑前台 loop
- `ExecutionRuntime`
  - 中文别名：后台执行 loop
- `SessionWorldState`
  - 中文别名：会话世界状态 / 当前局面板
- `StructuredProgressUpdate`
  - 中文别名：结构化进展更新
- `SessionTaskState`
  - 中文别名：会话任务状态
- `TaskChunkState`
  - 中文别名：任务 chunk 状态
- `PerceptionSummary`
  - 中文别名：感知摘要
- `ReplyStrategyState`
  - 中文别名：回复策略
- `DeliveryMode`
  - 中文别名：投递模式

除非明确说明，后文统一以这些正式名为准。

### 0.2 字段命名规则

- 所有协议字段统一使用 `snake_case`
- 单个 ID 字段统一使用 `_id`
  - 例如：`foreground_task_id`
- 多个 ID 字段统一使用 `_ids`
  - 例如：`background_task_ids`
- 状态对象统一使用 `_state`
  - 例如：`user_state`
- 摘要对象统一使用 `_summary`
  - 例如：`perception_summary`
- 风险标记统一使用 `_flags`
  - 例如：`risk_flags`
- 布尔字段统一使用明确语义
  - 例如：`user_visible`、`waiting_for_user`、`needs_tool`

### 0.3 字段所有权规则

- `MainBrainFrontLoop` 负责：
  - `conversation_phase`
  - `foreground_task_id`
  - `background_task_ids`
  - `reply_strategy`
  - 用户可见进展的最终收束
- `ExecutionRuntime` 负责：
  - `tasks[*].status`
  - `tasks[*].current_chunk`
  - `tasks[*].recent_observations`
  - `tasks[*].artifacts`
  - `StructuredProgressUpdate`
- 感知层 / 输入层负责：
  - `perception_summary`
- `Reflection` 不直接把 `SessionWorldState` 整体落成长期记忆

---

## 1. 总体边界

目标版架构只有一个真正的认知主体：`main brain`。

为了兼顾实时对话与后台重任务，系统拆成：

- `MainBrainFrontLoop`
  - 前台实时 loop
  - 负责快速理解、短响应、打断处理、最终表达
- `ExecutionRuntime`
  - 后台执行 loop
  - 负责 skill 调用、重任务推进、结构化结果回写
- `SessionWorldState`
  - 前后台共享状态面
  - 负责维护当前局面、多任务焦点、最近观察和回复策略
- `skills`
  - 主脑控制下的能力单元
  - 不是第二个脑
- `memory / reflection`
  - 负责长期沉淀，不参与当前轮前台表达

这个版本和“当前版左脑 / 右脑”最大的区别是：

- 不再把后台执行看成第二个认知主体
- 强调前台实时 loop 与后台执行 loop 都服务于同一个 `main brain`

### 1.1 当前正式存储面

目标版只使用这一套正式存储面：

| 层级 | 正式路径 | 说明 |
|------|----------|------|
| 对话历史 | `session/<session_id>/left.jsonl` | `用户 <-> 主脑前台` 原始对话历史 |
| 内部执行历史 | `session/<session_id>/right.jsonl` | `主脑 <-> 后台执行` 原始内部历史 |
| 短期记忆 | `memory/cognitive_events.jsonl` | 近期认知事件，承载浅反思与深反思的短期沉淀 |
| 长期记忆 | `memory/memory.jsonl` | 稳定事实、关系、经验的正式事实源 |
| 向量镜像 | `memory/vector/` | 长期记忆的检索镜像，不是事实源 |

强约束：

- `session/*.jsonl` 是历史，不是记忆
- `memory/cognitive_events.jsonl` 是短期记忆
- `memory/memory.jsonl` 是长期记忆
- `memory/vector/` 只是镜像层

---

## 2. `MainBrainFrontLoop` 模块契约

文件建议：

- `emoticorebot/main_brain/runtime.py`
- `emoticorebot/main_brain/context.py`
- `emoticorebot/main_brain/reply_policy.py`

### 2.1 职责

- 接收 turn / stream 输入
- 处理实时对话与短句响应
- 维持人格、情绪、关系一致性
- 读取 `SessionWorldState`
- 决定是否触发后台执行 loop
- 决定是否并行调用多个 skill
- 决定哪个任务进入前台焦点
- 将后台结构化结果转译成用户可见文本
- 保持最终表达权

### 2.2 强约束

- 不被重任务阻塞
- 不把原始工具轨迹直接暴露给用户
- 不把后台 loop 当成第二人格
- 对用户说的话必须从这里收束
- 决策可以参考长期记忆，但不会把执行中间态直接当成长记忆

### 2.3 它可以决定什么

- 当前轮直接回复还是触发后台执行
- 当前轮使用 `inline / stream / push` 哪种收束方式
- 当前前台应关注哪个任务
- 哪些后台结果值得对用户可见
- 什么时候需要追问、确认、延迟通知

### 2.4 它不做什么

- 不长时间占用执行线程跑重工具链
- 不直接管理原始命令执行细节
- 不直接保存长期记忆底层存储

---

## 3. `ExecutionRuntime` 模块契约

文件建议：

- `emoticorebot/execution/runtime.py`
- `emoticorebot/execution/executor.py`
- `emoticorebot/execution/chunking.py`
- `emoticorebot/execution/store.py`

### 3.1 职责

- 接受 `MainBrainFrontLoop` 触发的后台任务
- 管理一次或多次后台执行 run 的生命周期
- 调用 `skills`
- 收集结构化执行结果、进展、阻塞和产物
- 将结果写回 `SessionWorldState`
- 对长任务执行超时、取消、失败和重试策略

### 3.2 强约束

- 不直接对用户说话
- 不拥有最终任务语义
- 不直接写长期记忆
- 不直接决定哪个任务进入前台
- 不直接裸推日志、trace、OCR 原始结果给用户

### 3.3 它输出什么

`ExecutionRuntime` 应只输出结构化执行事件，例如：

- `task accepted`
- `task progress`
- `task blocked`
- `task waiting_user`
- `task result`

这些事件首先进入 `SessionWorldState`，再由 `MainBrainFrontLoop` 决定是否转成用户可见更新。

### 3.4 它不输出什么

- 最终对用户自然语言回复
- 没经筛选的工具轨迹
- 直接对外推送的原始调试信息

---

## 4. `skills` 模块契约

文件建议：

- `emoticorebot/skills/`
- `emoticorebot/tools/`

### 4.1 `skills` 的定位

`skills` 是主脑控制下的能力单元，不是第二个脑。

默认优先级：

- 优先 `一个主脑 agent + 一组 skills`
- 只在确实必要时才引入更重的多 agent / worker 语义

### 4.2 skill 应满足的条件

- 输入边界明确
- 输出结构化
- 不直接面向用户说话
- 不直接写长期记忆
- 能区分只读与有副作用调用

### 4.3 skill 建议分类

- 感知类
  - `transcribe_voice`
  - `extract_error_from_image`
  - `summarize_video`
- 查询类
  - `search_existing_issue`
  - `read_file`
  - `query_calendar`
- 动作类
  - `create_calendar_event`
  - `send_message`
  - `create_issue`
- 工程类
  - `run_command_and_capture_logs`
  - `inspect_repo_error_context`

### 4.4 强约束

- 有副作用 skill 必须带明确确认或权限边界
- 只读 skill 可以并行
- 多个 skill 可以并行运行，但由 `MainBrainFrontLoop` 决定是否并行以及如何收敛

---

## 5. `SessionWorldState` 模块契约

文件建议：

- `emoticorebot/session/models.py`
- `emoticorebot/session/runtime.py`
- 或新增 `emoticorebot/protocol/session_state.py`

### 5.1 定位

`SessionWorldState` 是当前会话的共享工作态。

它负责表示：

- 当前用户意图
- 当前对话阶段
- 当前用户状态
- 当前前台焦点任务
- 后台任务状态
- 最近一次感知结果
- 最近一次执行观察
- 当前回复策略

### 5.2 它是什么

- 当前局面板
- 前后台同步面
- 多任务可见性与焦点管理面

### 5.3 它不是什么

- 长期记忆
- 原始聊天日志
- 原始工具 trace 仓库
- 最终事实源

### 5.4 正式字段与枚举

```python
ConversationPhase = Literal[
    "idle",
    "chat",
    "multitask_chat",
    "support",
    "task_focus",
    "waiting_user",
    "crisis_response",
]

TaskStatus = Literal[
    "pending",
    "running",
    "waiting_user",
    "scheduled",
    "done",
    "failed",
    "cancelled",
]

TaskVisibility = Literal["silent", "concise", "verbose"]
TaskInterruptibility = Literal["never", "important_only", "always"]
TaskKind = Literal[
    "chat",
    "diagnosis",
    "reminder",
    "search",
    "analysis",
    "execution",
    "followup",
    "other",
]

UserEmotion = Literal[
    "neutral",
    "tired",
    "annoyed",
    "sad",
    "anxious",
    "happy",
    "excited",
    "despair",
]

UserEnergy = Literal["low", "medium", "high"]
ChunkStatus = Literal["pending", "running", "done", "failed", "blocked"]
DeliveryMode = Literal["inline", "push", "stream"]

class UserStateSnapshot(ProtocolModel):
    emotion: UserEmotion = "neutral"
    energy: UserEnergy = "medium"
    confidence: float = 0.0

class TaskChunkState(ProtocolModel):
    chunk_id: str
    title: str = ""
    status: ChunkStatus = "pending"

class PerceptionItemSummary(ProtocolModel):
    name: str = ""
    kind: str = ""
    status: str = ""
    summary: str = ""

class PerceptionSummary(ProtocolModel):
    images: list[PerceptionItemSummary] = Field(default_factory=list)
    audio: list[PerceptionItemSummary] = Field(default_factory=list)
    video: list[PerceptionItemSummary] = Field(default_factory=list)
    files: list[PerceptionItemSummary] = Field(default_factory=list)

class SessionTaskState(ProtocolModel):
    task_id: str
    title: str
    kind: TaskKind = "other"
    parent_task_id: str | None = None
    status: TaskStatus = "pending"
    priority: int = 50
    visibility: TaskVisibility = "concise"
    interruptibility: TaskInterruptibility = "important_only"
    user_visible: bool = True
    goal: str = ""
    current_chunk: TaskChunkState | None = None
    recent_observations: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    last_user_visible_update: str = ""
    waiting_for_user: bool = False
    risk_flags: list[str] = Field(default_factory=list)

class ReplyStrategyState(ProtocolModel):
    goal: str = ""
    style: str = ""
    delivery_mode: DeliveryMode = "inline"
    needs_tool: bool = False

class SessionWorldState(ProtocolModel):
    session_id: str
    conversation_phase: ConversationPhase = "idle"
    foreground_task_id: str | None = None
    background_task_ids: list[str] = Field(default_factory=list)
    user_state: UserStateSnapshot = Field(default_factory=UserStateSnapshot)
    active_topics: list[str] = Field(default_factory=list)
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    tasks: dict[str, SessionTaskState] = Field(default_factory=dict)
    perception_summary: PerceptionSummary = Field(default_factory=PerceptionSummary)
    reply_strategy: ReplyStrategyState = Field(default_factory=ReplyStrategyState)
    risk_flags: list[str] = Field(default_factory=list)
```

### 5.5 字段说明

| 字段 | 含义 | 主要写入方 |
|------|------|------------|
| `conversation_phase` | 当前会话处于哪种交互阶段 | `MainBrainFrontLoop` |
| `foreground_task_id` | 当前前台焦点任务 | `MainBrainFrontLoop` |
| `background_task_ids` | 后台推进但不抢占前台的任务 | `MainBrainFrontLoop` |
| `user_state` | 当前用户的情绪、能量和判断置信度 | `MainBrainFrontLoop` |
| `active_topics` | 当前轮仍在活跃的话题集合 | `MainBrainFrontLoop` |
| `confirmed_facts` | 当前会话里已经确认的结构化事实 | `MainBrainFrontLoop` |
| `open_questions` | 尚待确认的问题 | `MainBrainFrontLoop` |
| `tasks` | 多任务状态表 | `MainBrainFrontLoop` + `ExecutionRuntime` |
| `perception_summary` | 图片、音频、视频、文件的感知摘要 | 感知层 / 输入层 |
| `reply_strategy` | 当前回复目标、风格和投递模式 | `MainBrainFrontLoop` |
| `risk_flags` | 当前会话级风险标记 | `MainBrainFrontLoop` |

### 5.6 读写边界

- `MainBrainFrontLoop`
  - 可读可写
- `ExecutionRuntime`
  - 可读与局部写
  - 重点回写任务状态、观察结果、执行结果
- `reflection`
  - 可读，但不直接把它整体当成长记忆落盘

---

## 6. 多任务协议

### 6.1 目标

系统必须允许多个任务共存，但任何时刻只能有一个前台焦点任务。

### 6.2 核心字段

- `foreground_task_id`
  - 当前前台主线任务
- `background_task_ids`
  - 后台推进但不抢占前台的任务
- `tasks`
  - 所有任务状态表

### 6.3 每个任务正式字段

```python
class SessionTaskState(ProtocolModel):
    task_id: str
    title: str
    kind: TaskKind = "other"
    parent_task_id: str | None = None
    status: TaskStatus = "pending"
    priority: int = 50
    visibility: TaskVisibility = "concise"
    interruptibility: TaskInterruptibility = "important_only"
    user_visible: bool = True
    goal: str = ""
    current_chunk: TaskChunkState | None = None
    recent_observations: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    last_user_visible_update: str = ""
    waiting_for_user: bool = False
    risk_flags: list[str] = Field(default_factory=list)
```

### 6.4 任务字段说明

| 字段 | 含义 | 备注 |
|------|------|------|
| `task_id` | 任务唯一 ID | 会话内唯一 |
| `title` | 任务短标题 | 用于用户可见摘要 |
| `kind` | 任务类别 | 例如 `diagnosis`、`reminder` |
| `parent_task_id` | 父任务 ID | 可选，用于父子任务 |
| `status` | 任务运行状态 | 见 `TaskStatus` |
| `priority` | 优先级 | 建议 0-100 |
| `visibility` | 该任务进展默认对用户的可见性 | `silent/concise/verbose` |
| `interruptibility` | 该任务是否允许打断前台 | `never/important_only/always` |
| `user_visible` | 任务是否已进入用户可见层 | 可隐藏内部任务 |
| `goal` | 当前任务目标 | 供主脑和执行层共享理解 |
| `current_chunk` | 当前正在执行的 chunk | 可为空 |
| `recent_observations` | 最近结构化观察摘要 | 不等于原始日志 |
| `artifacts` | 当前任务产物引用 | 文件、链接、结构化产物 |
| `last_user_visible_update` | 最近一次已转译给用户的进展 | 用于防重复推送 |
| `waiting_for_user` | 当前是否卡在用户确认上 | 布尔态 |
| `risk_flags` | 任务级风险标记 | 如权限、失败、高风险内容 |

### 6.5 强约束

- 执行层可以并行推进多个任务
- 只有 `MainBrainFrontLoop` 决定哪个任务进入前台
- `visibility` 和 `interruptibility` 决定任务何时对用户可见

---

## 7. 进展可见性协议

### 7.1 问题定义

后台执行细节不是不能给用户看，而是不能未经筛选直接给。

### 7.2 正确链路

```text
ExecutionRuntime
  -> StructuredProgressUpdate
  -> SessionWorldState
  -> MainBrainFrontLoop
  -> User-visible progress
```

### 7.3 不正确链路

```text
ExecutionRuntime -> 直接对用户输出原始进展
```

### 7.4 正式进展字段

```python
class StructuredProgressUpdate(ProtocolModel):
    task_id: str
    stage: str = ""
    status: TaskStatus = "running"
    summary: str = ""
    observations: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    needs_user_input: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
```

### 7.5 进展字段说明

| 字段 | 含义 | 说明 |
|------|------|------|
| `task_id` | 关联任务 ID | 必填 |
| `stage` | 当前执行阶段 | 如 `ocr`、`issue_search`、`run_command` |
| `status` | 当前进展状态 | 通常为 `running` / `waiting_user` / `failed` / `done` |
| `summary` | 简洁阶段摘要 | 供主脑转译 |
| `observations` | 结构化观察摘要 | 不应直接填原始 trace |
| `artifacts` | 当前阶段产物 | 文件、链接、结构化输出 |
| `blockers` | 阻塞原因 | 可空 |
| `needs_user_input` | 是否需要用户确认 | 主脑据此决定追问 |
| `metadata` | 内部执行元数据 | 默认不直接对用户可见 |

### 7.6 可见性规则

- `silent`
  - 默认不主动告知，只更新状态
- `concise`
  - 只在关键阶段或结果时对用户可见
- `verbose`
  - 更多阶段可见，但仍由主脑转译

### 7.7 中断规则

- `never`
  - 不主动打断当前前台对话
- `important_only`
  - 只有关键结果、阻塞、确认请求时可见
- `always`
  - 可在任何阶段把更新带到前台

---

## 8. 实时对话与后台执行如何共存

### 8.1 前台实时 loop

前台 loop 负责：

- 接语音流和文本流
- 处理打断
- 快速接话
- 形成 `stream` / `inline` 短输出

### 8.2 后台执行 loop

后台 loop 负责：

- 处理 committed turn
- 执行重 skill
- 推进后台任务
- 产出结构化结果

### 8.3 共享原则

- 两个 loop 共享同一个 `SessionWorldState`
- 后台 loop 不抢最终表达权
- 前台 loop 可以在后台任务尚未结束时先给短反馈

---

## 9. 记忆与状态边界

### 9.1 历史层

历史层当前正式路径：

- `session/<session_id>/left.jsonl`
- `session/<session_id>/right.jsonl`

它存：

- 原始对话过程
- 原始内部执行过程

强约束：

- 它们是历史，不是记忆
- 它们可以作为反思输入，但不是长期事实源

### 9.2 短期记忆

短期记忆当前正式路径：

- `memory/cognitive_events.jsonl`

它存：

- 近期认知事件
- 浅反思结果
- 深反思阶段性结论
- 对当前决策仍然有价值的短期经验

强约束：

- `cognitive_events` 属于短期记忆
- 它不等于 `SessionWorldState`
- 它也不等于 `session/*.jsonl` 历史

### 9.3 长期记忆

长期记忆存：

- 用户事实
- 关系结论
- 人格与风格沉淀
- 经过治理的经验总结

长期记忆当前正式路径：

- `memory/memory.jsonl`

### 9.4 `SessionWorldState`

它存：

- 当前意图
- 当前任务与前台焦点
- 当前多模态观察
- 当前回复策略

强约束：

- 它是当前局面板，不是短期记忆文件
- 它和 `memory/cognitive_events.jsonl` 不能混用

### 9.5 向量镜像

向量镜像当前正式路径：

- `memory/vector/`

它负责：

- 长期记忆的检索加速
- 召回层的向量索引

强约束：

- 它不是事实源
- 它不替代 `memory/memory.jsonl`

### 9.6 执行中间态

执行 loop 还可以有内部 scratchpad：

- 原始日志
- OCR / ASR 原始输出
- 临时产物
- 调试元数据

强约束：

- 这些数据默认不直接写长期记忆
- 也不默认直接对用户可见

### 9.7 新版本反思写入原则

- `REFLECTION_LIGHT` / 浅反思
  - 以单轮 turn 为主
  - 主要产物是 `TurnReflectionOutput` 与 `CognitiveEventRecord`
  - 默认先进入 `memory/cognitive_events.jsonl`
- `REFLECTION_DEEP` / 深反思
  - 读取近期 `cognitive_events`
  - 负责把稳定结论提交到 `memory/memory.jsonl`
  - 负责抽取 `execution` / `skill_hint` / `workflow_pattern` 一类可复用经验
- `user_updates` / `soul_updates`
  - 是治理输入
  - 不是单独的记忆文件
- `memory/vector/`
  - 只镜像已提交的长期记忆
  - 不镜像 `session/*.jsonl`
  - 不镜像全部短期噪声

目标版强约束：

- 浅反思默认写短期，不把每个候选都直接视为长期记忆
- 深反思是长期沉淀的主路径
- 浅反思不直接提交 `memory/memory.jsonl`

### 9.8 浅反思字段

```python
ReflectionSourceType = Literal["user_turn", "task_event", "internal_task_event"]
ReflectionOutcome = Literal["success", "partial", "failed", "no_execution"]
ExecutionEffectiveness = Literal["high", "medium", "low", "none"]
MemoryType = Literal["relationship", "fact", "working", "execution", "reflection"]

class ReflectionStateUpdate(ProtocolModel):
    should_apply: bool = False
    confidence: float = 0.0
    reason: str = ""
    pad_state: dict[str, float] = Field(default_factory=dict)
    drives_state: dict[str, float] = Field(default_factory=dict)

class MemoryCandidate(ProtocolModel):
    memory_type: MemoryType = "reflection"
    summary: str = ""
    detail: str = ""
    confidence: float = 0.0
    stability: float = 0.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class ExecutionReview(ProtocolModel):
    effectiveness: ExecutionEffectiveness = "none"
    main_failure_reason: str = ""
    next_execution_hint: str = ""

class TurnReflectionOutput(ProtocolModel):
    summary: str = ""
    problems: list[str] = Field(default_factory=list)
    resolution: str = ""
    outcome: ReflectionOutcome = "no_execution"
    next_hint: str = ""
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)
    state_update: ReflectionStateUpdate = Field(default_factory=ReflectionStateUpdate)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    execution_review: ExecutionReview = Field(default_factory=ExecutionReview)
```

字段说明：

| 字段 | 含义 | 说明 |
|------|------|------|
| `summary` | 本轮反思摘要 | 一句话收束本轮 |
| `problems` | 本轮问题列表 | 没有问题可为空 |
| `resolution` | 问题如何解决 | 没解决也要如实写 |
| `outcome` | 本轮结果 | `success / partial / failed / no_execution` |
| `next_hint` | 下一轮承接提示 | 供主脑后续承接 |
| `user_updates` | 用户画像更新候选 | 治理输入，不等于已落盘 |
| `soul_updates` | 主脑风格更新候选 | 治理输入，不等于已落盘 |
| `state_update` | 本轮状态判断记录 | 记录本轮后状态应如何被描述 |
| `memory_candidates` | 长期记忆候选 | 目标版语义上默认先视为候选 |
| `execution_review` | 执行复盘 | 紧凑记录执行质量、失败原因、下一步提示 |

`state_update` 内部字段：

- `pad_state`
  - 记录本轮结束后主脑判断出的 PAD 状态值
- `drives_state`
  - 记录本轮结束后主脑判断出的 drives 状态值

### 9.9 `CognitiveEventRecord` 字段

```python
class MainBrainDecisionSnapshot(ProtocolModel):
    emotion: str = ""
    pad: dict[str, float] = Field(default_factory=dict)
    drives: dict[str, float] = Field(default_factory=dict)
    emotion_prompt: str = ""
    intent: str = ""
    working_hypothesis: str = ""
    retrieval_query: str = ""
    retrieval_focus: list[str] = Field(default_factory=list)
    retrieved_memory_ids: list[str] = Field(default_factory=list)
    task_request: str = ""
    task_action: str = ""
    task_reason: str = ""

class CognitiveEventRetrieval(ProtocolModel):
    query: str = ""
    memory_ids: list[str] = Field(default_factory=list)

class CognitiveEventMeta(ProtocolModel):
    importance: float = 0.5
    channel: str = ""
    source: str = ""
    source_type: ReflectionSourceType = "user_turn"
    message_id: str = ""

class CognitiveEventRecord(ProtocolModel):
    id: str
    schema_version: str = "cognitive_event.v1"
    timestamp: str
    session_id: str = ""
    turn_id: str = ""
    user_input: str = ""
    main_brain_state: MainBrainDecisionSnapshot = Field(default_factory=MainBrainDecisionSnapshot)
    retrieval: CognitiveEventRetrieval = Field(default_factory=CognitiveEventRetrieval)
    task: dict[str, Any] = Field(default_factory=dict)
    assistant_output: str = ""
    turn_reflection: TurnReflectionOutput = Field(default_factory=TurnReflectionOutput)
    meta: CognitiveEventMeta = Field(default_factory=CognitiveEventMeta)
```

字段说明：

| 字段 | 含义 | 说明 |
|------|------|------|
| `id` | 认知事件 ID | 短期记忆内唯一 |
| `schema_version` | 事件版本 | 当前目标版为 `cognitive_event.v1` |
| `timestamp` | 事件时间 | ISO 时间串 |
| `session_id` | 所属会话 | 可空，但建议保留 |
| `turn_id` | 所属轮次 | 会话内关联主键 |
| `user_input` | 用户输入摘要 | 原始输入的短保留 |
| `main_brain_state` | 主脑决策快照 | 主脑在这一轮的判断快照 |
| `retrieval` | 本轮检索摘要 | 检索查询和命中的记忆 ID |
| `task` | 本轮任务投影 | 当前保持开放字典 |
| `assistant_output` | 助手输出 | 本轮用户可见输出 |
| `turn_reflection` | 浅反思结果 | 对应 `TurnReflectionOutput` |
| `meta` | 认知事件元数据 | 重要度、来源、消息 ID 等 |

### 9.10 深反思字段

```python
class DeepReflectionProposal(ProtocolModel):
    summary: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)

class DeepReflectionResult(ProtocolModel):
    summary: str = ""
    memory_ids: list[str] = Field(default_factory=list)
    memory_count: int = 0
    skill_hint_count: int = 0
    materialized_skills: list[str] = Field(default_factory=list)
    materialized_skill_count: int = 0
    updated_soul: bool = False
    updated_user: bool = False
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)
```

字段说明：

| 字段 | 含义 | 说明 |
|------|------|------|
| `summary` | 深反思摘要 | 本轮批量沉淀的高层总结 |
| `memory_candidates` | 稳定长期记忆候选 | 深反思提交前的正式候选 |
| `memory_ids` | 已提交记忆 ID | 提交到 `memory/memory.jsonl` 后返回 |
| `memory_count` | 实际提交条数 | 去重后数量 |
| `skill_hint_count` | 其中技能提示类条数 | 一般来自 `metadata.subtype=skill_hint` |
| `materialized_skills` | 已物化技能名 | 若存在技能结晶 |
| `materialized_skill_count` | 技能物化数量 | 创建与更新合计 |
| `updated_soul` | 是否更新主脑风格治理结果 | 由治理层决定 |
| `updated_user` | 是否更新用户模型治理结果 | 由治理层决定 |
| `user_updates` | 用户画像治理候选 | 不是独立文件 |
| `soul_updates` | 主脑风格治理候选 | 不是独立文件 |

### 9.11 记忆记录字段

```python
MemoryStatus = Literal["active", "superseded", "invalid"]

class MemoryRecord(ProtocolModel):
    schema_version: str = "memory.record.v1"
    memory_id: str
    user_id: str = ""
    session_id: str = ""
    memory_type: MemoryType = "fact"
    summary: str = ""
    detail: str = ""
    evidence_messages: list[dict[str, Any]] = Field(default_factory=list)
    source_module: str = ""
    source_event_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.8
    stability: float = 0.5
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = "active"
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
```

字段说明：

| 字段 | 含义 | 说明 |
|------|------|------|
| `schema_version` | 记忆版本 | 当前目标版为 `memory.record.v1` |
| `memory_id` | 记忆 ID | 唯一主键 |
| `user_id` | 用户 ID | 可空 |
| `session_id` | 来源会话 | 可空 |
| `memory_type` | 正式记忆类型 | `relationship / fact / working / execution / reflection` |
| `summary` | 一句话摘要 | 检索与展示优先读这里 |
| `detail` | 完整提炼内容 | 正式事实内容 |
| `evidence_messages` | 证据消息列表 | 作为来源证据 |
| `source_module` | 来源模块 | 如 `reflection_governor.deep_reflection` |
| `source_event_ids` | 来源认知事件 ID | 可回溯来源 |
| `confidence` | 置信度 | 0 到 1 |
| `stability` | 稳定度 | 0 到 1 |
| `tags` | 标签列表 | 用于检索和过滤 |
| `status` | 生命周期状态 | `active / superseded / invalid` |
| `created_at` | 创建时间 | ISO 时间串 |
| `updated_at` | 更新时间 | ISO 时间串 |
| `metadata` | 类型扩展字段 | 放 subtype 与专有结构化负载 |

### 9.12 `MemoryRecord.metadata` 正式字段表

公共字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `subtype` | `str` | 是 | 记忆细分类型 |
| `importance` | `int` | 否 | 重要度，建议 `1-10` |
| `keywords` | `list[str]` | 否 | 检索关键词 |
| `tool_names` | `list[str]` | 否 | 相关工具名列表 |

正式 `metadata.subtype`：

- `relationship`
  - `user_model`
- `fact`
  - `profile_fact`
  - `environment_fact`
- `working`
  - `workflow_rule`
  - `collaboration_rule`
- `execution`
  - `workflow`
  - `skill_hint`
  - `tool_experience`
  - `error_pattern`
  - `workflow_pattern`
- `reflection`
  - `persona`
  - `turn_insight`

#### `relationship / user_model`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_traits` | `list[str]` | 否 | 用户稳定特征 |
| `preferences` | `list[str]` | 否 | 用户稳定偏好 |
| `boundaries` | `list[str]` | 否 | 用户长期边界 |

#### `fact / profile_fact`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `subject` | `str` | 是 | 事实主体 |
| `attribute` | `str` | 是 | 事实属性 |
| `value` | `str` | 是 | 属性值 |

#### `fact / environment_fact`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `entity` | `str` | 是 | 环境实体 |
| `attribute` | `str` | 是 | 环境属性 |
| `value` | `str` | 是 | 属性值 |

#### `working / workflow_rule`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `trigger` | `str` | 是 | 触发条件 |
| `rule` | `str` | 是 | 应遵循的工作规则 |
| `scope` | `str` | 否 | 规则适用范围 |

#### `working / collaboration_rule`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `trigger` | `str` | 是 | 协作触发条件 |
| `preferred_response` | `str` | 是 | 优先采用的协作方式 |
| `avoid` | `list[str]` | 否 | 应避免的做法 |

#### `execution / workflow`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `goal_cluster` | `str` | 是 | 工作流所属目标簇 |
| `tool_sequence` | `list[str]` | 否 | 常见工具顺序 |
| `preconditions` | `list[str]` | 否 | 前置条件 |
| `steps_summary` | `str` | 是 | 步骤摘要 |
| `sample_size` | `int` | 否 | 支撑样本数 |
| `success_rate` | `float` | 否 | 历史成功率，建议 `0-1` |

#### `execution / skill_hint`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `skill_id` | `str` | 是 | 技能 ID |
| `skill_name` | `str` | 是 | 技能名 |
| `trigger` | `str` | 是 | 触发条件 |
| `hint` | `str` | 是 | 技能提示正文 |
| `applies_to_tools` | `list[str]` | 否 | 适配工具列表 |

#### `execution / tool_experience`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool_name` | `str` | 是 | 工具名 |
| `experience_summary` | `str` | 是 | 经验摘要 |
| `success_condition` | `str` | 否 | 何种条件下更容易成功 |
| `failure_reason` | `str` | 否 | 常见失败原因 |

#### `execution / error_pattern`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `error_signature` | `str` | 是 | 错误特征或错误文本签名 |
| `failure_reason` | `str` | 是 | 失败原因 |
| `fix_hint` | `str` | 是 | 修复提示 |
| `applies_to_tools` | `list[str]` | 否 | 相关工具列表 |

#### `execution / workflow_pattern`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pattern_name` | `str` | 是 | 工作流模式名 |
| `trigger` | `str` | 是 | 触发条件 |
| `steps_summary` | `str` | 是 | 模式步骤摘要 |
| `sample_size` | `int` | 否 | 支撑样本数 |
| `success_rate` | `float` | 否 | 历史成功率，建议 `0-1` |

#### `reflection / persona`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `style_rule` | `str` | 是 | 稳定风格规则 |
| `scenario` | `str` | 否 | 适用场景 |
| `preferred_behavior` | `str` | 否 | 优先行为 |

#### `reflection / turn_insight`

附加字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `problem` | `str` | 否 | 本轮核心问题 |
| `resolution` | `str` | 是 | 解决方式 |
| `outcome` | `str` | 是 | 结果，如 `success / partial / failed / no_execution` |
| `follow_up` | `str` | 否 | 后续提示 |

---

## 10. 当前代码的目标映射

### 10.1 目标代码目录结构

```text
emoticorebot/
├── main_brain/
│   ├── runtime.py
│   ├── context.py
│   ├── planner.py
│   ├── reply_policy.py
│   └── perception.py
├── execution/
│   ├── runtime.py
│   ├── executor.py
│   ├── chunking.py
│   ├── progress.py
│   └── store.py
├── session/
│   ├── runtime.py
│   ├── models.py
│   └── thread_store.py
├── memory/
│   ├── store.py
│   ├── retrieval.py
│   ├── vector_index.py
│   └── crystallizer.py
├── reflection/
│   ├── runtime.py
│   ├── governor.py
│   ├── manager.py
│   ├── turn.py
│   ├── deep.py
│   └── cognitive.py
├── skills/
├── tools/
├── protocol/
├── input/
├── output/
├── delivery/
└── safety/
```

### 10.2 目标运行数据目录结构

```text
workspace/
├── memory/
│   ├── cognitive_events.jsonl
│   ├── memory.jsonl
│   └── vector/
├── session/
│   └── <session_id>/
│       ├── left.jsonl
│       └── right.jsonl
└── skills/
    └── <skill_name>/
        └── SKILL.md
```

### 10.3 `main_brain/runtime.py`

目标演进：

- 作为 `MainBrainFrontLoop` 主入口
- 负责前台实时 loop 与主调度

### 10.4 `main_brain/context.py`

目标演进：

- 负责主脑上下文拼装
- 汇聚 `SessionWorldState`、短期记忆、长期记忆与感知摘要

### 10.5 `main_brain/reply_policy.py`

目标演进：

- 负责回复策略、投递模式与用户可见收束

### 10.6 `execution/runtime.py`

目标演进：

- 作为 `ExecutionRuntime` 主入口
- 负责后台任务生命周期、chunk 推进与进展回写

### 10.7 `execution/executor.py`

目标演进：

- 负责单次 chunk 执行
- 负责 skill / tool 调用收束

### 10.8 `session/runtime.py`

目标演进：

- 负责 `SessionWorldState` 持有与同步
- 负责历史索引与会话内任务状态落位

### 10.9 `memory/retrieval.py`

目标演进：

- 继续主要服务于 `main brain`
- 后台执行 loop 默认不直接检索长期记忆全量上下文

---

## 11. 文档关系

这份文档是目标版模块契约草案，对应：

- [docs/companion-main-brain-execution-architecture.zh-CN.md](docs/companion-main-brain-execution-architecture.zh-CN.md)

这份文档应独立成立，不依赖其他旧版架构文档。

---

## 12. 与架构文档的统一规则

为避免不同文档之间字段漂移，目标版文档遵循以下规则：

- 组件正式名以本文件 `0.1` 为准
- `SessionWorldState` 正式字段以本文件 `5.4` 为准
- `SessionTaskState` 正式字段以本文件 `6.3` 为准
- `StructuredProgressUpdate` 正式字段以本文件 `7.4` 为准
- `TurnReflectionOutput` 正式字段以本文件 `9.8` 为准
- `CognitiveEventRecord` 正式字段以本文件 `9.9` 为准
- `MemoryRecord` 正式字段以本文件 `9.11` 为准
- 架构文档中的 JSON 例子和自然语言描述，如与这里冲突，以本文件为准

---

## 13. 一句话版本

目标版契约的一句话是：

`main brain` 统一拥有任务语义与最终表达权；后台执行 loop 只负责技能执行与状态回写；`SessionWorldState` 负责把多任务、多模态观察、进展可见性和实时对话同步成一个当前局面。
