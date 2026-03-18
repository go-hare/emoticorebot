# 陪伴机器人代码层协议正式规范

## 1. 文档定位

本文定义本项目唯一正式的代码层协议。

这份协议服务于同一个主体脑的内部协作，核心模块固定为：

- `Left Brain`
- `Right Brain`
- `Reflection`
- `Memory`
- `Session Supervisor`
- `Delivery Plane`

本文不再讨论兼容旧架构，也不以旧命名作为目标定义。  
代码中现有类名和目录名如果与本文不同，应以后者为准逐步收敛。

配套文档：

- [companion-left-right-brain-architecture.zh-CN.md](companion-left-right-brain-architecture.zh-CN.md)
- [companion-left-right-brain-module-contracts.zh-CN.md](companion-left-right-brain-module-contracts.zh-CN.md)

---

## 2. 唯一协议原则

### 2.1 唯一主体

- 对外只有一个陪伴主体。
- 左脑和右脑不是两个对外人格。
- 所有用户可见输出都必须回到同一主体口吻。

### 2.2 唯一输入抽象

系统只承认两类输入：

- `turn`
- `stream`

### 2.3 唯一投递抽象

系统只承认三类投递：

- `inline`
- `push`
- `stream`

### 2.4 唯一后台抽象

所有理性分析、执行、工具、长耗时处理都属于 `Right Brain`。  
不再单独引入产品层面的第三套“任务系统”概念。

### 2.5 唯一事件边界

- 模块之间通过事件协作
- 状态读取通过只读查询或快照
- 用户可见语义只在左脑与投递层收束

### 2.6 唯一入脑顺序

- 所有原始输入先进入 `Session Supervisor`
- `Session Supervisor` 先发出 `left.command.reply_requested`
- 左脑先解析 `user/task` 双槽并收敛本轮右脑策略
- `Session Supervisor` 再依据左脑结果决定是否发出 `right.command.job_requested`
- 左脑可按需启用内建评分辅助，但不是入口强依赖

---

## 3. 协议分层

代码层协议固定分成 6 层：

1. `transport`
2. `input`
3. `intent`
4. `left brain`
5. `right brain`
6. `output / memory / reflection`

### 3.1 `transport`

负责外部通道接入，不负责业务判断。

### 3.2 `input`

把通道输入统一成：

- `turn`
- `stream.start`
- `stream.chunk`
- `stream.commit`
- `stream.interrupt`

### 3.3 `intent`

可选小模型辅助层，只给：

- 分数
- 标签
- 左右脑启动计划
- 路由提示

### 3.4 `left brain`

负责：

- 陪伴表达
- 低延迟回复
- 流式接话
- 统一人格口吻
- 解析 `user/task` 双槽
- 收敛 `right_brain_strategy`

### 3.5 `right brain`

负责：

- 审查可做性
- 深度推理
- 工具调用
- 异步执行
- 结果整理

### 3.6 `output / memory / reflection`

- `output` 负责投递
- `memory` 负责存取
- `reflection` 负责长期演化

---

## 4. 正式 Topic 定义

协议层以以下 topic 为正式定义：

| Topic | 用途 |
|------|------|
| `input.event` | 输入事件 |
| `intent.event` | 意图评分事件 |
| `left.command` | 左脑请求 |
| `left.event` | 左脑结果 |
| `right.command` | 右脑请求 |
| `right.event` | 右脑结果 |
| `output.event` | 统一输出事件 |
| `memory.signal` | 记忆读写 |
| `reflection.event` | 反思事件 |
| `system.signal` | 系统信号 |

---

## 5. 正式 EventType 定义

### 5.1 输入层

| EventType | 含义 |
|------|------|
| `input.event.turn_received` | 收到单轮输入 |
| `input.event.stream_started` | 流开始 |
| `input.event.stream_chunk` | 流增量 |
| `input.event.stream_committed` | 流提交片段 |
| `input.event.stream_interrupted` | 流中断 |

### 5.2 意图层

| EventType | 含义 |
|------|------|
| `intent.event.scored` | 小模型评分完成 |

### 5.3 左脑层

| EventType | 含义 |
|------|------|
| `left.command.reply_requested` | 请求左脑生成回复 |
| `left.event.reply_ready` | 左脑生成完整回复 |
| `left.event.stream_delta_ready` | 左脑生成流式片段 |
| `left.event.followup_ready` | 左脑基于右脑结果生成补充回复 |

### 5.4 右脑层

| EventType | 含义 |
|------|------|
| `right.command.job_requested` | 请求右脑受理 |
| `right.event.job_accepted` | 右脑接受处理 |
| `right.event.job_clarify` | 右脑需要补充信息 |
| `right.event.job_rejected` | 右脑拒绝处理 |
| `right.event.result_ready` | 右脑产出结果 |

### 5.5 输出层

| EventType | 含义 |
|------|------|
| `output.event.inline_ready` | 当前轮立即投递 |
| `output.event.push_ready` | 异步推送投递 |
| `output.event.stream_open` | 输出流开始 |
| `output.event.stream_delta` | 输出流增量 |
| `output.event.stream_close` | 输出流结束 |

### 5.6 记忆与反思

| EventType | 含义 |
|------|------|
| `memory.signal.write_request` | 请求写入记忆 |
| `memory.signal.write_committed` | 记忆写入完成 |
| `reflection.event.turn_requested` | 单轮反思 |
| `reflection.event.deep_requested` | 深反思 |

---

## 6. 正式 Payload 归属

### 6.1 `protocol/task_models.py`

这里承载跨模块共享的基础模型。

必须包含：

- `ProtocolModel`
- `ContentBlock`
- `MessageRef`
- `InputRequest`
- `PlanStep`
- `ReviewItem`
- `ReplyDraft`
- `TaskRequestSpec`
- `TaskStateSnapshot`
- `ProvidedInputItem`
- `ProvidedInputBundle`
- `AgentInputContext`
- `ReviewerContext`
- `ControlParameters`
- `PerceptionData`

### 6.2 `protocol/events.py`

这里承载所有事件 payload。

必须包含：

- 输入事件 payload
- 意图事件 payload
- 左脑结果 payload
- 右脑结果 payload
- 输出事件 payload
- 记忆与反思 payload

### 6.3 `protocol/commands.py`

这里承载所有命令 payload。

必须包含：

- 左脑请求命令
- 右脑请求命令
- 会话控制命令
- 控制层命令

### 6.4 `protocol/topics.py`

这里承载唯一正式的 `Topic` 与 `EventType` 常量。

### 6.5 `protocol/contracts.py` 与 `protocol/event_contracts.py`

- `contracts.py` 承载跨模块共享的一级协议枚举：
  - `InputMode / SessionMode / ChannelKind / InputKind`
  - `DeliveryMode / ReplyDeliveryMode / StreamState`
  - `RightBrainStrategy / RightBrainJobAction`
- `event_contracts.py` 承载 `EventType -> PayloadModel` 唯一映射。
- `BusEnvelope` 在构造阶段必须校验：
  - topic 与 event type 一致
  - payload 类型与 event type 契约一致

---

## 7. 正式输入协议

### 7.1 `TurnInputPayload`

推荐先把原始文本拆成双槽：

```text
#######user#######
你好
#######task#######
r任务相关
```

```json
{
  "input_mode": "turn",
  "session_mode": "turn_chat",
  "channel_kind": "chat",
  "input_kind": "text",
  "message": {
    "channel": "telegram",
    "chat_id": "123456",
    "sender_id": "user_1",
    "message_id": "msg_1"
  },
  "user_text": "你帮我整理一下这份笔记。",
  "input_slots": {
    "user": "你帮我整理一下这份笔记。",
    "task": "整理笔记，整理完后通知我"
  },
  "content_blocks": [],
  "attachments": [],
  "metadata": {}
}
```

### 7.2 `StreamStartPayload`

```json
{
  "input_mode": "stream",
  "session_mode": "realtime_chat",
  "stream_id": "stream_1",
  "message": {
    "channel": "call",
    "chat_id": "room_1",
    "sender_id": "user_1",
    "message_id": "stream_msg_1"
  },
  "metadata": {}
}
```

### 7.3 `StreamChunkPayload`

```json
{
  "input_mode": "stream",
  "stream_id": "stream_1",
  "chunk_index": 12,
  "chunk_text": "我最近睡得不太好",
  "is_commit_point": false,
  "metadata": {}
}
```

### 7.4 `StreamCommitPayload`

```json
{
  "input_mode": "stream",
  "stream_id": "stream_1",
  "committed_text": "我最近睡得不太好",
  "metadata": {}
}
```

---

## 8. 可选意图协议

### 8.1 `IntentScoredPayload`

```json
{
  "input_mode": "turn",
  "session_mode": "turn_chat",
  "source_text": "我今天真的好累。",
  "scores": {
    "affective_score": 0.93,
    "rational_score": 0.14,
    "task_score": 0.01,
    "urgency_score": 0.24,
    "risk_score": 0.09,
    "realtime_score": 0.18,
    "confidence": 0.91
  },
  "intent_tags": ["comfort"],
  "emotion_tags": ["fatigue"],
  "route_hint": "left_only",
  "input_slots": {
    "user": "我今天真的好累。",
    "task": ""
  },
  "right_brain_strategy": "skip",
  "invoke_right_brain": false,
  "reason": "当前输入是情绪表达。"
}
```

### 8.2 入口调度语义

- `intent.event.scored` 不是唯一必经事件，而是可选辅助事件
- 左脑可以直接基于 `user/task` 双槽做策略收敛
- `Session Supervisor` 的正式调度依据是 `left.event.reply_ready`
- `invoke_right_brain=true` 时发出 `right.command.job_requested`
- 左脑后续若要补充升级右脑参与，也必须重新经 `Session Supervisor` 发命令

### 8.3 强约束

可选评分辅助只负责：

- 评分
- 标签
- 启动计划
- 路由提示

可选评分辅助不负责：

- 最终回复
- 深度执行决策
- 长链推理
- 左脑最终裁决

---

## 9. 正式左脑协议

### 9.1 左脑输入命令

#### `LeftBrainReplyRequest`

```json
{
  "request_id": "left_req_1",
  "input_mode": "turn",
  "session_mode": "turn_chat",
  "source_text": "你帮我整理一下这份笔记。",
  "input_slots": {
    "user": "你帮我整理一下这份笔记。",
    "task": "整理笔记，整理完后通知我"
  },
  "scores": {
    "affective_score": 0.18,
    "rational_score": 0.63,
    "task_score": 0.89,
    "urgency_score": 0.27,
    "risk_score": 0.06,
    "realtime_score": 0.09,
    "confidence": 0.92
  },
  "dispatch_plan": {
    "start_right_brain": true,
    "right_brain_strategy": "async",
    "dispatch_reason": "task 槽非空，建议触发右脑后台处理。"
  },
  "relationship": {
    "familiarity_level": 0.72,
    "trust_level": 0.68,
    "tone_preference": "warm",
    "recent_emotion": "neutral"
  },
  "memory_context": {
    "recent_facts": ["用户最近在整理课程资料"]
  }
}
```

### 9.2 左脑输出事件

左脑只输出两类内容：

- 当前轮可见回复
- 对右脑的启动请求

#### `LeftReplyReadyPayload`

```json
{
  "reply_text": "可以，我先帮你整理，整理好后发给你。",
  "delivery_target": {
    "delivery_mode": "inline"
  },
  "right_brain_strategy": "async",
  "invoke_right_brain": true,
  "right_brain_request": {
    "job_kind": "execution_review",
    "request_text": "帮用户整理这份笔记",
    "expected_delivery_mode": "push"
  },
  "memory_candidate": {
    "kind": "working",
    "summary": "用户请求整理笔记。"
  }
}
```

#### `right_brain_strategy` 正式定义

在 `turn` 模式下，左脑必须基于 `user/task` 双槽与上下文显式收敛右脑策略：

| 字段值 | 含义 |
|------|------|
| `skip` | 当前轮不启动右脑 |
| `sync` | 当前轮同步等待右脑 |
| `async` | 当前轮结束后由右脑后台处理 |

网页对话场景默认优先使用：

- `skip`
- `sync`

只有明确的执行型请求、长处理请求，或用户接受“稍后通知”时，才使用：

- `async`

#### `LeftStreamDeltaPayload`

```json
{
  "stream_id": "stream_1",
  "delta_text": "嗯，我在听。",
  "stream_state": "delta"
}
```

### 9.3 左脑强约束

- 左脑是唯一前台表达主体
- 左脑可以启动右脑
- 左脑不能把最终表达权交给右脑

---

## 10. 正式右脑协议

### 10.1 右脑输入命令

#### `RightBrainJobRequest`

```json
{
  "job_id": "job_1",
  "job_kind": "execution_review",
  "source_text": "你帮我整理一下这份笔记。",
  "request_text": "帮用户整理这份笔记",
  "scores": {
    "affective_score": 0.18,
    "rational_score": 0.63,
    "task_score": 0.89,
    "urgency_score": 0.27,
    "risk_score": 0.06,
    "realtime_score": 0.09,
    "confidence": 0.92
  },
  "delivery_target": {
    "delivery_mode": "push",
    "channel": "telegram",
    "chat_id": "123456"
  },
  "context": {
    "history_summary": "用户最近在整理课程资料"
  }
}
```

### 10.2 右脑输出事件

#### `RightBrainAcceptedPayload`

```json
{
  "job_id": "job_1",
  "decision": "accept",
  "stage": "plan",
  "reason": "请求明确且可执行。",
  "estimated_duration_s": 15
}
```

#### `RightBrainClarifyPayload`

```json
{
  "job_id": "job_1",
  "decision": "clarify",
  "question": "你想要我整理成摘要、提纲，还是按章节重写？",
  "missing_fields": ["output_format"],
  "reason": "输出格式不明确。"
}
```

#### `RightBrainRejectedPayload`

```json
{
  "job_id": "job_1",
  "decision": "reject",
  "reason": "缺少可处理的内容源文件。"
}
```

#### `RightBrainResultPayload`

```json
{
  "job_id": "job_1",
  "decision": "accept",
  "summary": "笔记整理完成。",
  "result_text": "我已经按 5 个知识点整理好了。",
  "delivery_target": {
    "delivery_mode": "push",
    "channel": "telegram",
    "chat_id": "123456"
  },
  "memory_candidate": {
    "kind": "execution",
    "summary": "用户笔记整理请求已完成。"
  }
}
```

### 10.3 右脑强约束

- 右脑负责后台理性与执行
- 右脑可以审查“能不能做、该不该做”
- 右脑不能直接越过左脑面向用户发言

### 10.4 `turn` 模式下右脑的正式工作模式

#### `skip`

- 不创建右脑作业
- 当前轮完全由左脑完成

#### `sync`

- 创建轻量右脑作业
- 左脑等待其结果
- 当前轮内给出统一回复

#### `async`

- 创建后台右脑作业
- 当前轮先结束
- 完成后由左脑生成 `push` 输出

---

## 11. 正式输出协议

### 11.1 `inline`

#### `OutputInlineReadyPayload`

```json
{
  "output_id": "out_1",
  "delivery_target": {
    "delivery_mode": "inline",
    "channel": "telegram",
    "chat_id": "123456"
  },
  "content": {
    "reply_id": "reply_1",
    "kind": "answer",
    "plain_text": "听起来你今天真的很累。"
  }
}
```

### 11.2 `push`

#### `OutputPushReadyPayload`

```json
{
  "output_id": "out_2",
  "delivery_target": {
    "delivery_mode": "push",
    "channel": "telegram",
    "chat_id": "123456"
  },
  "content": {
    "reply_id": "reply_2",
    "kind": "status",
    "plain_text": "我整理好了，发你一版简洁提纲。"
  },
  "metadata": {
    "job_id": "job_1"
  }
}
```

### 11.3 `stream`

#### `OutputStreamOpenPayload`

```json
{
  "output_id": "out_stream_1",
  "delivery_target": {
    "delivery_mode": "stream",
    "channel": "call",
    "chat_id": "room_1"
  },
  "stream_id": "stream_1",
  "stream_state": "open",
  "content": {
    "reply_id": "reply_stream_1",
    "kind": "answer",
    "plain_text": "嗯。"
  }
}
```

#### `OutputStreamDeltaPayload`

```json
{
  "output_id": "out_stream_2",
  "delivery_target": {
    "delivery_mode": "stream",
    "channel": "call",
    "chat_id": "room_1"
  },
  "stream_id": "stream_1",
  "stream_state": "delta",
  "content": {
    "reply_id": "reply_stream_2",
    "kind": "answer",
    "plain_text": "我在听，你继续说。"
  }
}
```

#### `OutputStreamClosePayload`

```json
{
  "output_id": "out_stream_3",
  "delivery_target": {
    "delivery_mode": "stream",
    "channel": "call",
    "chat_id": "room_1"
  },
  "stream_id": "stream_1",
  "stream_state": "close",
  "content": {
    "reply_id": "reply_stream_3",
    "kind": "answer",
    "plain_text": "要不要我陪你一起把这件事拆开说清楚？"
  }
}
```

### 11.4 网页对话的正式推荐

网页对话默认属于 `turn` 输入形态。

正式推荐如下：

| 场景 | 推荐组合 |
|------|------|
| 普通网页聊天 | `turn + inline` |
| 网页 SSE 打字效果 | `turn + stream` |
| 网页提交长处理请求，稍后站内通知 | `turn + push` |

在网页对话里，右脑策略默认优先级为：

1. `skip`
2. `sync`
3. `async`

也就是说，网页对话允许同步等待右脑，但不应默认把所有请求都变成后台作业。

---

## 12. 正式记忆与反思协议

### 12.1 Memory

必须支持：

- 关系记忆
- 事实记忆
- 工作态记忆
- 执行记忆
- 反思记忆

### 12.2 Reflection

必须只做：

- 轮后反思
- 深反思
- 人格与关系提炼

不得阻塞当前轮前台输出。

---

## 13. 代码文件正式落点

以下是正式的代码落点要求。

### 13.1 `protocol/task_models.py`

作为唯一基础模型定义文件。

### 13.2 `protocol/events.py`

作为唯一事件 payload 文件。

### 13.3 `protocol/commands.py`

作为唯一命令 payload 文件。

### 13.4 `protocol/topics.py`

作为唯一 topic / event_type 文件。

### 13.5 `session/runtime.py`

正式定义为 `Session Supervisor` 的承载文件。

### 13.6 `brain/executive.py`

正式定义为左脑的核心承载文件。

### 13.7 `task/runtime.py`

正式定义为右脑后台运行时的核心承载文件。

### 13.8 `delivery/*`

正式定义为三类投递的统一投递层。

---

## 14. 当前代码命名与正式语义

为避免歧义，正式语义如下：

| 当前代码名 | 正式语义 |
|------|------|
| `ExecutiveBrain` | `Left Brain` |
| `TaskRuntime` | `Right Brain Runtime` |
| `DeepAgentExecutor` | `Right Brain Executor` |
| `SessionRuntime` | `Session Supervisor` |
| `DeliveryService` | `Delivery Plane` |

这里是正式定性，不是临时解释。

---

## 15. `create_task` 的正式语义

`create_task` 不再被理解为“必然执行一个任务”，而被正式定义为：

> 向右脑提交一个待受理请求。

右脑对该请求拥有以下正式裁决权：

- `accept`
- `answer_only`
- `clarify`
- `reject`

因此：

- 用户请求进入右脑，不等于右脑已经承诺执行
- 右脑先审查，再决定执行、追问、拒绝或仅返回理性答案

---

## 16. 正式实施纪律

从本文开始，后续实现应遵循以下纪律：

1. 新代码以本文协议命名为准
2. 左脑与右脑边界必须继续收敛，而不是回退成“大总管式脑”
3. `turn / stream` 与 `inline / push / stream` 视为一级协议概念，不得再降级成临时 metadata 习惯用法
4. 所有新增模块、事件、状态字段，都应优先写入 `protocol/*` 和文档，而不是先散落到运行时代码中

---

## 17. 最终定义

本项目唯一正式的代码层协议定义为：

> 一个统一主体脑，内部由左脑和右脑协同工作。  
> 输入只分 `turn / stream`，投递只分 `inline / push / stream`。  
> 左脑负责前台表达，右脑负责后台理性与执行，反思和记忆负责长期连续性。  
> `Session Supervisor` 负责调度，`Delivery Plane` 负责投递，`protocol/*` 负责唯一正式协议定义。

---

## 18. 实施进度（2026-03-18）

### 18.1 已完成

1. 输入主链路已收敛到 `turn`：
   - 入口统一发布 `input.event.turn_received`
   - `Session Supervisor` 统一发布 `left.command.reply_requested`
   - 左脑统一消费 `LeftReplyRequestPayload.turn_input`
2. 输出主链路已迁移到三投递事件：
   - `output.event.inline_ready`
   - `output.event.push_ready`
   - `output.event.stream_open / stream_delta / stream_close`
3. 安全拦截已改为“同事件类型内处理”：
   - 脱敏后保持原输出事件类型
   - 不再拆分为旧的 approved/redacted 事件族
4. 协议层已完成契约冻结（v1）：
   - 新增 `protocol/contracts.py` 统一定义一级协议枚举
   - 新增 `protocol/event_contracts.py` 统一定义 `EventType -> PayloadModel`
   - `BusEnvelope` 已增加事件类型与 payload 类型一致性校验
5. 输入一级字段已从 metadata 升级为显式字段：
   - `TurnInputPayload.channel_kind`
   - `TurnInputPayload.input_kind`
6. 投递模式已收敛为强类型：
   - `ReplyReadyPayload.delivery_mode = inline|push|stream`
   - `RepliedPayload.delivery_mode = inline|push|stream|suppressed`
7. 当前代码状态已通过测试验收：
   - `pytest`：`148 passed, 2 skipped`（2026-03-18）

### 18.2 未完成

1. `stream` 输入主链路尚未打通到业务运行时：
   - `input.event.stream_started/chunk/committed/interrupted` 已定义
   - 但当前运行时代码中尚无对应生产与消费链路
2. `intent.event.scored` 仍处于预留态：
   - 协议模型与事件常量已定义
   - 当前无实际发布者与订阅者
3. 左脑扩展事件仍处于预留态：
   - `left.event.stream_delta_ready`
   - `left.event.followup_ready`
   - 当前未进入运行时事件流
4. 右脑 `clarify` 事件未落地：
   - `right.event.job_clarify` 已定义
   - 当前右脑运行时仅稳定发出 `accepted / rejected / result_ready`
5. `right_brain_strategy=sync` 语义尚未严格化：
   - 目前会发起右脑作业
   - 但前台回复仍按当前轮直接发出，尚未实现“同步等待右脑结果再回包”的严格语义
6. `right.event.result_ready` 目前仅发出，尚未形成独立消费闭环：
   - 目前前台通知仍主要依赖任务事件 (`task.event.ask/end`) 回灌左脑
   - 后续应明确 `right.result -> left.followup -> output.push` 的专用通路是否启用

