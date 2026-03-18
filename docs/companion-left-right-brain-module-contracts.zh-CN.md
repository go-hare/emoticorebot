# 陪伴机器人模块契约与字段清单

## 1. 文档目的

本文是 [companion-left-right-brain-architecture.zh-CN.md](companion-left-right-brain-architecture.zh-CN.md) 的工程细化版。

目标是把“左脑 / 右脑、双输入、三投递”的高层设计，落实成：

- 公共字段定义
- 模块输入输出契约
- 状态字段
- 事件载荷
- 样例 JSON

本文不是实现代码，而是未来代码重构时要尽量保持稳定的接口设计。

---

## 2. 设计边界

### 2.1 对外只有一个主体

- 用户只面对一个陪伴主体。
- 左脑和右脑都是内部模块，不直接暴露成两个角色。
- 所有用户可见回复，最终都应带有统一主体口吻。

### 2.2 模块之间通过事件协作

- 模块之间优先通过事件解耦。
- 只读状态查询可以通过查询接口直接读取。
- 跨模块不应直接依赖具体类名和内部方法。

### 2.3 字段稳定优先

后续可以更换：

- 小模型
- 主模型
- DeepAgent
- 记忆实现
- 交付通道

但尽量不随意更改本文里的字段语义。

### 2.4 唯一入口顺序

- 所有原始输入先进入 `Session Supervisor`
- `Session Supervisor` 必须先把输入交给左脑处理
- 左脑必须先解析 `user/task` 双槽，再收敛本轮 `right_brain_strategy`
- `task` 槽为空时，默认 `right_brain_strategy=skip`
- `task` 槽非空时，左脑可选择 `sync/async` 并构造 `right_brain_request`
- 左脑可按需启用内建评分辅助，但不是入口强依赖

---

## 3. 公共枚举

### 3.1 输入模式

| 字段值 | 含义 |
|------|------|
| `turn` | 单轮输入 |
| `stream` | 持续流输入 |

### 3.2 投递模式

| 字段值 | 含义 |
|------|------|
| `inline` | 当前轮直接返回 |
| `push` | 当前轮结束后再推送 |
| `stream` | 持续流式输出 |

### 3.3 会话模式

| 字段值 | 含义 |
|------|------|
| `turn_chat` | 单轮聊天模式 |
| `realtime_chat` | 实时流式对话模式 |

### 3.4 左脑路由建议

| 字段值 | 含义 |
|------|------|
| `left_only` | 左脑直接处理 |
| `left_with_right` | 左脑先处理，同时启动右脑 |
| `right_review_first` | 先让右脑审看，再由左脑表达 |
| `clarify_first` | 先追问 |
| `safe_fallback` | 进入安全降级 |

### 3.5 右脑受理结果

| 字段值 | 含义 |
|------|------|
| `accept` | 接受并执行 |
| `answer_only` | 不执行，只给左脑答案素材 |
| `clarify` | 需要补充信息 |
| `reject` | 不应处理或不能处理 |

### 3.6 输出流状态

| 字段值 | 含义 |
|------|------|
| `open` | 流开始 |
| `delta` | 增量片段 |
| `close` | 正常结束 |
| `superseded` | 被中断或替换 |

### 3.7 记忆类型

| 字段值 | 含义 |
|------|------|
| `relationship` | 关系与偏好记忆 |
| `fact` | 事实记忆 |
| `working` | 工作态上下文 |
| `execution` | 执行痕迹 |
| `reflection` | 反思结论 |

---

## 4. 公共标识字段

所有模块都尽量复用下列公共 ID。

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | `string` | 跨通道稳定用户标识，没有时可为空 |
| `session_id` | `string` | 当前会话标识 |
| `turn_id` | `string` | 当前单轮处理标识 |
| `stream_id` | `string` | 当前实时流标识 |
| `job_id` | `string` | 右脑后台作业标识 |
| `output_id` | `string` | 一次输出动作标识 |
| `message_id` | `string` | 用户原始消息或系统投递消息标识 |
| `correlation_id` | `string` | 跨模块关联追踪标识 |
| `causation_id` | `string` | 引发当前事件的上游事件 ID |
| `timestamp` | `string` | ISO 8601 时间戳 |

建议：

- `turn_id` 只在 `turn` 输入中稳定存在
- `stream_id` 只在 `stream` 输入中稳定存在
- `job_id` 只属于右脑后台作业

---

## 5. 公共引用结构

### 5.1 `MessageRef`

```json
{
  "channel": "telegram",
  "chat_id": "123456",
  "sender_id": "user_1",
  "message_id": "msg_abc",
  "reply_to_message_id": "msg_prev",
  "timestamp": "2026-03-18T12:00:00Z"
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `channel` | `string` | 否 | 通道名 |
| `chat_id` | `string` | 否 | 会话路由 ID |
| `sender_id` | `string` | 否 | 发送者标识 |
| `message_id` | `string` | 否 | 消息 ID |
| `reply_to_message_id` | `string` | 否 | 回复目标 ID |
| `timestamp` | `string` | 否 | 消息时间 |

### 5.2 `ScoreBundle`

```json
{
  "affective_score": 0.84,
  "rational_score": 0.33,
  "task_score": 0.18,
  "urgency_score": 0.22,
  "risk_score": 0.05,
  "realtime_score": 0.71,
  "confidence": 0.90
}
```

所有评分约定：

- 范围：`0.0 ~ 1.0`
- 越大表示对应倾向越强
- 不要求总和为 `1.0`

### 5.3 `DeliveryTarget`

```json
{
  "delivery_mode": "push",
  "channel": "telegram",
  "chat_id": "123456",
  "thread_id": "",
  "push_priority": "normal"
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `delivery_mode` | `string` | 是 | `inline/push/stream` |
| `channel` | `string` | 否 | 目标通道 |
| `chat_id` | `string` | 否 | 目标会话 |
| `thread_id` | `string` | 否 | 子线程或子房间 |
| `push_priority` | `string` | 否 | `low/normal/high` |

---

## 6. 可选辅助评分（左脑内建）

### 6.1 定位

这里描述的是左脑内建的可选评分能力，不是独立对外模块。

它只看用户输入和最少量上下文，不负责生成最终回复，也不替代左脑最终裁决。

### 6.2 输入

#### 输入事件

- `input.turn.received`
- `input.stream.chunk`
- `input.stream.committed`

#### 输入载荷

```json
{
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "stream_id": "",
  "input_mode": "turn",
  "channel_kind": "chat",
  "input_kind": "text",
  "message": {
    "channel": "telegram",
    "chat_id": "123456",
    "sender_id": "user_1",
    "message_id": "msg_1"
  },
  "user_text": "我今天好累，不想做任何事。",
  "input_slots": {
    "user": "我今天好累，不想做任何事。",
    "task": ""
  },
  "recent_summary": "用户最近压力较大，昨晚睡眠不好。",
  "session_mode": "turn_chat"
}
```

### 6.3 输出

#### 输出事件

- `intent.scored`

#### 输出载荷

```json
{
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "stream_id": "",
  "input_mode": "turn",
  "scores": {
    "affective_score": 0.92,
    "rational_score": 0.18,
    "task_score": 0.03,
    "urgency_score": 0.27,
    "risk_score": 0.22,
    "realtime_score": 0.30,
    "confidence": 0.91
  },
  "intent_tags": ["comfort", "emotion_disclosure"],
  "emotion_tags": ["fatigue", "sadness"],
  "route_hint": "left_only",
  "input_slots": {
    "user": "我今天好累，不想做任何事。",
    "task": ""
  },
  "right_brain_strategy": "skip",
  "invoke_right_brain": false,
  "reason": "当前输入主要是情绪表达，不像执行请求。"
}
```

### 6.4 左脑可消费字段

该能力仅输出给左脑可消费的辅助字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `input_slots` | `object` | 推荐的 `user/task` 双槽结构 |
| `right_brain_strategy` | `string` | 建议策略：`skip / sync / async` |
| `invoke_right_brain` | `bool` | 建议是否触发右脑 |
| `reason` | `string` | 说明文本，便于日志与调试 |

入口调度规则：

- `intent.scored` 若存在，先交由左脑作为参考
- `left.event.reply_ready` 才是 `Session Supervisor` 的正式调度依据
- `invoke_right_brain=true` 时，发出 `right.command.job_requested`
- 若左脑后续需要补充升级右脑策略，仍通过 `Session Supervisor` 二次发出右脑命令

### 6.5 状态字段

该能力不建议维护复杂状态，只保留轻量运行信息：

| 字段 | 说明 |
|------|------|
| `model_name` | 当前使用的小模型名 |
| `model_version` | 模型版本 |
| `last_latency_ms` | 最近一次识别耗时 |
| `fallback_mode` | 是否退化为规则模式 |

### 6.6 判断边界

该能力可以做：

- 多维评分
- 意图标签
- 情绪标签
- 左脑辅助建议
- 路由提示

该能力不应做：

- 最终对用户说的话
- 高价值执行决策
- 深度安全结论
- 长链推理

---

## 7. 模块二：Left Brain

### 7.1 定位

`Left Brain` 是用户真正感知到的前台系统。

职责重点：

- 快
- 稳
- 有陪伴感
- 统一人格

### 7.2 输入

#### 输入来源

- `input.turn.received`
- `input.stream.chunk`
- `input.stream.committed`
- `right.result.ready`
- `right.job.clarify`
- `right.job.rejected`
- `session.interrupt`

#### 左脑输入结构

推荐文本模板先解析为 `input_slots.user / input_slots.task`：

```text
#######user#######
你好
#######task#######
r任务相关
```

```json
{
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "stream_id": "",
  "input_mode": "turn",
  "session_mode": "turn_chat",
  "channel_kind": "chat",
  "input_kind": "text",
  "input_slots": {
    "user": "你能帮我整理一下这份笔记吗？",
    "task": "整理会议笔记，整理完成后通知我"
  },
  "left_brain_judgement": {
    "right_brain_strategy": "async",
    "invoke_right_brain": true,
    "reason": "task 槽非空，且属于后台执行型请求。"
  },
  "user_text": "你能帮我整理一下这份笔记吗？",
  "relationship_context": {
    "tone_preference": "warm",
    "familiarity_level": 0.72,
    "recent_emotion": "neutral"
  },
  "memory_context": {
    "user_preferences": ["不喜欢太官话", "偏好简洁"],
    "recent_facts": ["用户本周在整理课程资料"]
  }
}
```

### 7.3 输出

左脑输出不是只有“回复文本”，而应包含：

- 对用户的话
- 右脑参与方式
- 推荐投递方式
- 记忆写入候选

这里要注意：

- 左脑先给前台回复，再决定右脑参与方式
- 左脑可以在当前轮补充升级右脑策略
- 所有右脑启动都必须经 `Session Supervisor` 收束成正式右脑命令

#### 左脑输出结构

```json
{
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "stream_id": "",
  "reply_text": "可以，我先帮你整理，整理好后发给你。",
  "reply_style": {
    "warmth": 0.74,
    "directness": 0.68,
    "verbosity": 0.28
  },
  "delivery_plan": {
    "delivery_mode": "inline"
  },
  "right_brain_strategy": "async",
  "invoke_right_brain": true,
  "right_brain_request": {
    "job_kind": "execution_review",
    "request_text": "帮用户整理这份笔记",
    "expected_result_mode": "push"
  },
  "memory_candidate": {
    "type": "working",
    "summary": "用户请求整理一份笔记。"
  }
}
```

### 7.4 左脑核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `reply_text` | `string` | 当前要给用户的文本 |
| `reply_blocks` | `array` | 多模态或分块内容 |
| `reply_style` | `object` | 话术风格控制 |
| `delivery_plan` | `object` | 当前回复投递方式 |
| `right_brain_strategy` | `string` | 当前轮对右脑的最终策略收敛值 |
| `invoke_right_brain` | `bool` | 是否确认或追加右脑参与 |
| `right_brain_request` | `object` | 右脑受理请求 |
| `memory_candidate` | `object` | 候选记忆摘要 |
| `safety_level` | `string` | 当前回复安全等级 |

### 7.5 `reply_style` 字段

```json
{
  "warmth": 0.8,
  "directness": 0.5,
  "verbosity": 0.3,
  "empathy": 0.9,
  "rational_weight": 0.2
}
```

字段说明：

| 字段 | 范围 | 说明 |
|------|------|------|
| `warmth` | `0~1` | 温柔感 |
| `directness` | `0~1` | 直接程度 |
| `verbosity` | `0~1` | 详细程度 |
| `empathy` | `0~1` | 共情程度 |
| `rational_weight` | `0~1` | 回复中理性占比 |

### 7.6 左脑状态字段

建议 `Left Brain` 维护的运行状态：

| 字段 | 说明 |
|------|------|
| `active_stream_id` | 当前活跃输出流 |
| `last_reply_text` | 最近一次对用户可见回复 |
| `last_reply_at` | 最近一次回复时间 |
| `last_right_brain_job_id` | 最近触发的右脑作业 |
| `pending_followup` | 是否存在待处理右脑回流 |

### 7.7 左脑边界

左脑可以：

- 快速接住用户
- 做情绪表达
- 决定前台话术
- 启动右脑
- 将右脑结果包装后对外说出

左脑不适合：

- 独立长链执行
- 重度工具操作
- 长时间阻塞等待

### 7.8 `turn` 模式下左脑对右脑的调用策略

在 `turn` 模式下，左脑必须基于 `user/task` 双槽与上下文，为右脑收敛出以下三种策略之一：

- `skip`
- `sync`
- `async`

建议将其显式写入左脑输出。

#### 新增字段：`right_brain_strategy`

```json
{
  "right_brain_strategy": "sync"
}
```

字段定义：

| 字段值 | 含义 |
|------|------|
| `skip` | 当前轮不启动右脑 |
| `sync` | 当前轮同步等待右脑轻量结果 |
| `async` | 当前轮先结束，右脑后台继续 |

#### 更新后的左脑输出结构

```json
{
  "reply_text": "我先想一下。",
  "delivery_plan": {
    "delivery_mode": "inline"
  },
  "invoke_right_brain": true,
  "right_brain_strategy": "sync",
  "right_brain_request": {
    "job_kind": "reasoning_review",
    "request_text": "判断用户问题应如何回答"
  }
}
```

#### 选择规则

- 简单陪聊：`skip`
- 轻量分析：`sync`
- 长耗时或执行型处理：`async`

网页对话场景通常优先使用：

- `skip`
- `sync`

只有在用户明确希望“处理完再通知我”时，才优先选择：

- `async`

---

## 8. 模块三：Right Brain

### 8.1 定位

`Right Brain` 是异步后台系统，可以使用 `DeepAgent` 作为执行引擎。

右脑的本质是：

- 理性处理
- 执行受理
- 工具和长耗时逻辑

### 8.2 输入

#### 输入事件

- `right.job.requested`

#### 输入载荷

```json
{
  "job_id": "job_1",
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "correlation_id": "corr_1",
  "job_kind": "execution_review",
  "request_text": "帮用户整理这份笔记",
  "source_text": "你能帮我整理一下这份笔记吗？",
  "scores": {
    "affective_score": 0.22,
    "rational_score": 0.61,
    "task_score": 0.87,
    "urgency_score": 0.31,
    "risk_score": 0.06,
    "realtime_score": 0.10,
    "confidence": 0.90
  },
  "delivery_target": {
    "delivery_mode": "push",
    "channel": "telegram",
    "chat_id": "123456"
  },
  "context": {
    "history_summary": "用户本周在整理课程资料",
    "memory_refs": ["mem_1", "mem_2"]
  }
}
```

### 8.3 右脑处理阶段

建议右脑内部按统一阶段推进：

| 阶段 | 含义 |
|------|------|
| `review` | 受理审查 |
| `plan` | 执行规划 |
| `execute` | 执行 |
| `clarify` | 等待补充 |
| `done` | 完成 |
| `rejected` | 拒绝 |

### 8.4 右脑第一层输出：受理结果

#### 事件

- `right.job.accepted`
- `right.job.clarify`
- `right.job.rejected`

#### `accepted` 载荷

```json
{
  "job_id": "job_1",
  "session_id": "sess_1",
  "decision": "accept",
  "reason": "这是一个明确且可执行的整理请求。",
  "stage": "plan",
  "estimated_cost": "low",
  "estimated_duration_s": 12
}
```

#### `clarify` 载荷

```json
{
  "job_id": "job_1",
  "session_id": "sess_1",
  "decision": "clarify",
  "question": "你想让我整理成提纲、摘要，还是按章节重写？",
  "missing_fields": ["output_format"],
  "reason": "输出格式不明确。"
}
```

#### `rejected` 载荷

```json
{
  "job_id": "job_1",
  "session_id": "sess_1",
  "decision": "reject",
  "reason": "当前请求缺少可供处理的内容源文件。",
  "suggested_left_brain_mode": "answer_only"
}
```

### 8.5 右脑第二层输出：最终结果

#### 事件

- `right.result.ready`

#### 载荷

```json
{
  "job_id": "job_1",
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "result_type": "execution_result",
  "decision": "accept",
  "summary": "笔记已经整理完成。",
  "result_text": "我已经按知识点归类整理好了，一共分成 5 个部分。",
  "artifacts": [
    {
      "artifact_id": "artifact_1",
      "artifact_type": "text",
      "name": "整理后的笔记",
      "uri": ""
    }
  ],
  "delivery_target": {
    "delivery_mode": "push",
    "channel": "telegram",
    "chat_id": "123456"
  },
  "memory_candidate": {
    "type": "execution",
    "summary": "用户笔记整理请求已完成。"
  }
}
```

### 8.6 右脑核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `job_id` | `string` | 后台作业 ID |
| `job_kind` | `string` | 作业类型 |
| `decision` | `string` | `accept/answer_only/clarify/reject` |
| `stage` | `string` | 当前处理阶段 |
| `summary` | `string` | 对内部与左脑可读的摘要 |
| `result_text` | `string` | 最终结果文字 |
| `artifacts` | `array` | 产物列表 |
| `missing_fields` | `array` | 缺失字段 |
| `delivery_target` | `object` | 建议投递目标 |
| `memory_candidate` | `object` | 候选记忆 |

### 8.7 右脑状态字段

| 字段 | 说明 |
|------|------|
| `job_queue_size` | 当前队列长度 |
| `active_jobs` | 活跃作业数 |
| `last_job_started_at` | 最近一次启动时间 |
| `last_job_completed_at` | 最近一次完成时间 |
| `per_session_job_counts` | 每个 session 的后台作业数 |

### 8.8 右脑边界

右脑可以：

- 审查可做性
- 工具调用
- 长耗时处理
- 给左脑补理性结果

右脑不应：

- 自己直接对用户说话
- 绕过左脑发送最终回复

### 8.9 `turn` 模式下右脑的正式行为

#### 当 `right_brain_strategy = skip`

- 右脑不启动
- 不创建后台作业

#### 当 `right_brain_strategy = sync`

- 右脑启动一个轻量作业
- 左脑在当前轮等待该结果
- 当前轮结果仍以 `inline` 或 `turn + stream` 返回

#### 当 `right_brain_strategy = async`

- 右脑启动后台作业
- 左脑当前轮先结束
- 右脑完成后再生成 `push` 结果

---

## 9. 模块四：Reflection

### 9.1 定位

`Reflection` 是慢速、事后、长期的认知演化模块。

### 9.2 输入

#### 输入事件

- `output.inline.ready`
- `output.push.ready`
- `output.stream.close`
- `right.result.ready`

#### 输入载荷

```json
{
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "user_text": "我今天好累。",
  "visible_reply": "听起来你今天真的消耗很大，要不要先歇一会儿？",
  "right_brain_summary": "",
  "relationship_snapshot": {
    "familiarity_level": 0.72,
    "trust_level": 0.66
  }
}
```

### 9.3 输出

#### 事件

- `reflection.turn.requested`
- `reflection.deep.requested`
- `memory.write.requested`

#### 反思输出结构

```json
{
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "reflection_type": "turn",
  "insights": [
    "用户在高压力下更偏好被先安抚，再讨论解决方案。"
  ],
  "memory_candidates": [
    {
      "type": "relationship",
      "summary": "用户疲惫时更偏好温柔承接，不喜欢一上来分析。"
    }
  ],
  "persona_adjustments": [
    {
      "field": "comfort_first",
      "delta": 0.1
    }
  ]
}
```

### 9.4 反思核心字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `reflection_type` | `string` | `turn/deep` |
| `insights` | `array` | 反思结论 |
| `memory_candidates` | `array` | 记忆写入建议 |
| `persona_adjustments` | `array` | 人格调整建议 |
| `confidence` | `float` | 反思置信度 |

### 9.5 反思状态字段

| 字段 | 说明 |
|------|------|
| `last_turn_reflection_at` | 最近一轮反思时间 |
| `last_deep_reflection_at` | 最近一次深反思时间 |
| `pending_reflection_count` | 待处理反思数量 |

---

## 10. 模块五：Memory

### 10.1 定位

`Memory` 保存对陪伴主体有价值的长期和工作态信息。

### 10.2 读取接口

建议定义统一查询接口，不让左脑和右脑直接碰底层存储细节。

#### 查询请求

```json
{
  "session_id": "sess_1",
  "user_id": "user_1",
  "query_text": "用户当前为什么会情绪低落",
  "memory_types": ["relationship", "fact"],
  "limit": 5
}
```

#### 查询响应

```json
{
  "items": [
    {
      "memory_id": "mem_1",
      "type": "relationship",
      "summary": "用户最近两周工作压力偏高。",
      "confidence": 0.86
    }
  ]
}
```

### 10.3 写入接口

#### 写入请求

```json
{
  "memory_id": "mem_new_1",
  "session_id": "sess_1",
  "user_id": "user_1",
  "type": "relationship",
  "summary": "用户疲惫时更偏好被先安抚。",
  "source": "reflection",
  "confidence": 0.81,
  "tags": ["comfort_preference", "stress"]
}
```

### 10.4 记忆条目字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `memory_id` | `string` | 记忆 ID |
| `type` | `string` | 记忆类型 |
| `summary` | `string` | 摘要 |
| `detail` | `string` | 详细内容 |
| `source` | `string` | 来源模块 |
| `confidence` | `float` | 可信度 |
| `tags` | `array` | 标签 |
| `created_at` | `string` | 创建时间 |
| `updated_at` | `string` | 更新时间 |

### 10.5 记忆状态字段

| 字段 | 说明 |
|------|------|
| `relationship_memory_count` | 关系记忆数量 |
| `fact_memory_count` | 事实记忆数量 |
| `working_memory_count` | 工作态条目数量 |
| `last_compaction_at` | 最近整理时间 |

---

## 11. 模块六：Session Supervisor

### 11.1 定位

`Session Supervisor` 是每个会话的调度中心。

它不负责“想什么”，只负责“谁先做、什么时候做、怎么投递”。

### 11.2 核心状态字段

```json
{
  "session_id": "sess_1",
  "user_id": "user_1",
  "session_mode": "realtime_chat",
  "input_mode": "stream",
  "active_turn_id": "",
  "active_stream_id": "stream_1",
  "active_left_output_id": "out_1",
  "active_right_jobs": ["job_1"],
  "pending_push_count": 1,
  "archived": false
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `session_mode` | 当前会话模式 |
| `input_mode` | 当前输入模式 |
| `active_turn_id` | 当前单轮 ID |
| `active_stream_id` | 当前流 ID |
| `active_left_output_id` | 左脑当前输出 ID |
| `active_right_jobs` | 活跃右脑作业列表 |
| `pending_push_count` | 待推送数 |
| `archived` | 是否已归档 |

### 11.3 输入

#### 会话监督器接收的事件

- `input.turn.received`
- `input.stream.started`
- `input.stream.chunk`
- `session.interrupt`
- `right.result.ready`
- `output.stream.close`

### 11.4 输出

#### 会话监督器发出的事件

- `intent.score.requested`
- `left.reply.requested`
- `right.job.requested`
- `output.inline.ready`
- `output.push.ready`
- `output.stream.open/delta/close`

### 11.5 监督器边界

监督器可以：

- 控并发
- 控模式
- 控中断
- 决定投递路径

监督器不应：

- 自己写最终回复
- 自己做深度推理

---

## 12. 模块七：Delivery Plane

### 12.1 定位

`Delivery Plane` 统一负责把内部结果投递到外部通道。

### 12.2 输入

#### 输入事件

- `output.inline.ready`
- `output.push.ready`
- `output.stream.open`
- `output.stream.delta`
- `output.stream.close`

### 12.3 输出字段

#### 统一投递请求

```json
{
  "output_id": "out_1",
  "session_id": "sess_1",
  "delivery_mode": "push",
  "message_ref": {
    "channel": "telegram",
    "chat_id": "123456"
  },
  "content": {
    "text": "我整理好了，发你一版简洁提纲。",
    "blocks": []
  },
  "stream_state": "",
  "metadata": {
    "from": "left_brain",
    "job_id": "job_1"
  }
}
```

### 12.4 投递状态字段

| 字段 | 说明 |
|------|------|
| `last_delivery_at` | 最近一次投递时间 |
| `last_delivery_channel` | 最近投递通道 |
| `delivery_fail_count` | 失败次数 |
| `pending_retry_count` | 待重试数量 |

### 12.5 交付边界

投递层可以：

- 适配通道协议
- 处理重试
- 处理流状态

投递层不应：

- 修改回复语义
- 决定是否执行右脑

---

## 13. 建议事件清单与字段

说明：本节事件名以工程示例为主；代码层正式常量命名以
`companion-protocol-spec.zh-CN.md` 的 `Topic/EventType` 定义为准。

### 13.1 `input.turn.received`

```json
{
  "event_type": "input.turn.received",
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "input_mode": "turn",
  "channel_kind": "chat",
  "input_kind": "text",
  "message": {},
  "user_text": "帮我整理一下这份笔记。",
  "input_slots": {
    "user": "帮我整理一下这份笔记。",
    "task": "整理笔记并在完成后通知我"
  },
  "timestamp": "2026-03-18T12:00:00Z"
}
```

### 13.2 `input.stream.chunk`

```json
{
  "event_type": "input.stream.chunk",
  "session_id": "sess_1",
  "stream_id": "stream_1",
  "input_mode": "stream",
  "chunk_index": 12,
  "chunk_text": "我今天其实有点难受",
  "is_commit_point": false
}
```

### 13.3 `intent.scored`（可选）

```json
{
  "event_type": "intent.scored",
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "scores": {},
  "intent_tags": [],
  "emotion_tags": [],
  "route_hint": "left_with_right"
}
```

### 13.4 `right.job.requested`

```json
{
  "event_type": "right.job.requested",
  "job_id": "job_1",
  "session_id": "sess_1",
  "turn_id": "turn_1",
  "job_kind": "execution_review",
  "request_text": "帮用户整理笔记",
  "delivery_target": {}
}
```

### 13.5 `right.result.ready`

```json
{
  "event_type": "right.result.ready",
  "job_id": "job_1",
  "session_id": "sess_1",
  "summary": "笔记整理完成",
  "result_text": "我已经按 5 个知识点整理好了。",
  "delivery_target": {}
}
```

### 13.6 `output.push.ready`

```json
{
  "event_type": "output.push.ready",
  "output_id": "out_1",
  "session_id": "sess_1",
  "delivery_mode": "push",
  "message_ref": {},
  "content": {
    "text": "我整理好了，发你一版简洁提纲。"
  }
}
```

---

## 14. 三个典型样例

### 14.1 纯陪聊：左脑直接回复

用户输入：

```json
{
  "user_text": "我今天好累。"
}
```

左脑判定：

```json
{
  "affective_score": 0.93,
  "rational_score": 0.16,
  "task_score": 0.01,
  "input_slots": {
    "user": "我今天好累。",
    "task": ""
  },
  "right_brain_strategy": "skip"
}
```

左脑输出：

```json
{
  "reply_text": "听起来你今天真的消耗很大，要不要先歇一会儿？",
  "delivery_plan": {
    "delivery_mode": "inline"
  },
  "invoke_right_brain": false
}
```

### 14.2 单轮触发后台处理

用户输入：

```json
{
  "user_text": "你帮我整理好这份会议笔记，整理好后告诉我。"
}
```

左脑判定：

```json
{
  "affective_score": 0.10,
  "rational_score": 0.58,
  "task_score": 0.95,
  "input_slots": {
    "user": "你帮我整理好这份会议笔记，整理好后告诉我。",
    "task": "整理会议笔记并完成后通知"
  },
  "right_brain_strategy": "async"
}
```

左脑输出：

```json
{
  "reply_text": "可以，我先整理，整理完发给你。",
  "delivery_plan": {
    "delivery_mode": "inline"
  },
  "invoke_right_brain": true,
  "right_brain_request": {
    "job_kind": "execution_review",
    "expected_result_mode": "push"
  }
}
```

右脑完成后：

```json
{
  "summary": "会议笔记整理完成",
  "result_text": "我已经按议题、结论和待办整理好了。",
  "delivery_target": {
    "delivery_mode": "push"
  }
}
```

### 14.3 实时对话中右脑补理性

用户输入流中某一段：

```json
{
  "chunk_text": "我最近老睡不好，是不是作息出问题了"
}
```

左脑判定（可选叠加小模型）：

```json
{
  "affective_score": 0.61,
  "rational_score": 0.74,
  "task_score": 0.20,
  "realtime_score": 0.91,
  "input_slots": {
    "user": "我最近老睡不好，是不是作息出问题了",
    "task": ""
  },
  "right_brain_strategy": "sync"
}
```

左脑先给实时回应：

```json
{
  "reply_text": "嗯，我在听。睡不好这件事最近持续多久了？",
  "delivery_plan": {
    "delivery_mode": "stream"
  },
  "invoke_right_brain": true
}
```

右脑稍后回流：

```json
{
  "summary": "右脑判断应先做信息澄清，不应直接下结论。",
  "decision": "answer_only"
}
```

---

## 15. 推荐实现顺序

1. 固定字段与事件名
2. 先落地左脑 `user/task` 双槽解析
3. 将前台表达明确实现为 `Left Brain`
4. 将后台理性与执行明确实现为 `Right Brain`
5. 在入口层显式实现 `turn / stream`
6. 在投递层显式实现 `inline / push / stream`
7. 左脑内建评分辅助作为可选优化接入

### 15.4 第四阶段

- 再做更细的实时流仲裁
- 再做更细的反思和记忆治理

---

## 16. 最终约束

无论以后怎么改实现，都建议保持以下约束不变：

1. 用户只面对一个主体
2. 左脑始终负责前台表达
3. 右脑始终负责异步理性与执行
4. 左脑先解析 `user/task` 再决定右脑策略
5. 小模型若使用，只做辅助评分与提示
6. 反思不阻塞当前轮
7. `turn / stream` 与 `inline / push / stream` 作为一级协议概念长期保留

