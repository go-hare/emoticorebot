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
- 左脑直接基于输入与上下文完成当前轮策略收敛

---

## 3. 协议分层

代码层协议固定分成 5 层：

1. `transport`
2. `input`
3. `left brain`
4. `right brain`
5. `output / memory / reflection`

### 3.1 `transport`

负责外部通道接入，不负责业务判断。

### 3.2 `input`

把通道输入统一成：

- `turn`
- `stream.start`
- `stream.chunk`
- `stream.commit`
- `stream.interrupt`

### 3.3 `left brain`

负责：

- 陪伴表达
- 低延迟回复
- 流式接话
- 统一人格口吻
- 解析 `user/task` 双槽
- 收敛 `right_brain_strategy`

### 3.4 `right brain`

负责：

- 审核钩子
- 深度推理
- 工具调用
- 异步执行
- DeepAgent 生命周期控制
- 结果整理

### 3.5 `output / memory / reflection`

- `output` 负责投递
- `memory` 负责存取
- `reflection` 负责长期演化

---

## 4. 正式 Topic 定义

协议层以以下 topic 为正式定义：

| Topic | 用途 |
|------|------|
| `input.event` | 输入事件 |
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

### 5.2 左脑层

| EventType | 含义 |
|------|------|
| `left.command.reply_requested` | 请求左脑生成回复 |
| `left.event.reply_ready` | 左脑生成完整回复 |
| `left.event.stream_delta_ready` | 左脑生成流式片段 |
| `left.event.followup_ready` | 左脑基于右脑结果生成补充回复 |

### 5.3 右脑层

| EventType | 含义 |
|------|------|
| `right.command.job_requested` | 请求右脑受理 |
| `right.event.job_accepted` | 右脑接受处理 |
| `right.event.progress` | 右脑过程进展 |
| `right.event.job_rejected` | 右脑拒绝处理 |
| `right.event.result_ready` | 右脑产出结果 |

### 5.4 输出层

| EventType | 含义 |
|------|------|
| `output.event.inline_ready` | 当前轮立即投递 |
| `output.event.push_ready` | 异步推送投递 |
| `output.event.stream_open` | 输出流开始 |
| `output.event.stream_delta` | 输出流增量 |
| `output.event.stream_close` | 输出流结束 |

### 5.5 记忆与反思

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

### 7.5 `StreamInterruptPayload`

```json
{
  "input_mode": "stream",
  "stream_id": "stream_1",
  "reason": "user_stopped",
  "metadata": {}
}
```

## 9. 正式左脑协议

### 9.1 左脑输入命令

#### `LeftBrainReplyRequest`

```json
{
  "request_id": "left_req_1",
  "turn_input": {
    "input_id": "turn_1",
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
    "metadata": {}
  },
  "metadata": {}
}
```

`LeftBrainReplyRequest` 作为左脑统一入口命令，既可由用户原始输入触发，也可由右脑回流触发。

当本次左脑处理来源于右脑回流时，应附带可选的 `followup_context`：

```json
{
  "request_id": "left_req_2",
  "followup_context": {
    "source_event": "right.event.result_ready",
    "job_id": "job_1",
    "decision": "accept",
    "summary": "笔记整理完成。",
    "result_text": "我已经按 5 个知识点整理好了。",
    "preferred_delivery_mode": "push"
  }
}
```

约束：

- `followup_context` 只描述右脑回流素材，不替代左脑最终表达
- `source_event` 正式只允许：
  - `right.event.job_accepted`
  - `right.event.progress`
  - `right.event.job_rejected`
  - `right.event.result_ready`

### 9.2 左脑输出事件

左脑只输出两类内容：

- 用户可见回复（含当前轮与右脑回流后的 followup）
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

#### `LeftFollowupReadyPayload`

```json
{
  "job_id": "job_1",
  "source_event": "right.event.result_ready",
  "source_decision": "accept",
  "reply_text": "我已经整理好了，发你一版简洁提纲。",
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

`left.event.followup_ready` 是左脑承接右脑回流后的唯一正式用户可见事件。

它用于把以下右脑结果重新收束成统一主体口吻：

- `right.event.job_accepted`
- `right.event.progress`
- `right.event.result_ready`
- `right.event.job_rejected`

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
  "right_brain_strategy": "async",
  "job_action": "create_task",
  "source_text": "你帮我整理一下这份笔记。",
  "request_text": "帮用户整理这份笔记",
  "goal": "整理课程笔记并在完成后通知用户",
  "context": {
    "title": "整理课程笔记",
    "expected_output": "一版按主题归类的提纲",
    "recent_turns": [
      {
        "role": "user",
        "content": "你帮我整理一下这份笔记。"
      },
      {
        "role": "assistant",
        "content": "可以，我先处理，过程中会持续告诉你进展。"
      }
    ],
    "short_term_memory": ["用户本周在整理课程资料"],
    "long_term_memory": ["用户偏好简洁提纲"],
    "tool_context": {
      "available_tools": ["read_note", "write_outline"],
      "tool_constraints": ["不要直接面向用户输出"]
    }
  },
  "metadata": {
    "left_request_id": "left_req_1",
    "left_reply_kind": "status"
  }
}
```

#### `RightBrainJobRequest.context` 约束

- `recent_turns` 建议最多只带最近 `10` 轮，避免把整段会话原样灌进 `DeepAgent`
- `tool_context` 只传当前 run 真正相关的工具摘要与约束，不要求传全量工具细节
- `short_term_memory / long_term_memory` 只传摘要与引用，不要求把记忆库原文整体注入
- `RightBrainRuntime` 收到请求后应立即启动同一种 `DeepAgent` run；`sync / async` 只影响左脑等待与投递策略

### 10.2 右脑输出事件

#### `RightBrainAcceptedPayload`

```json
{
  "job_id": "job_1",
  "decision": "accept",
  "stage": "execute",
  "reason": "audit_tool 返回任务可以开始。",
  "estimated_duration_s": 15
}
```

#### `RightBrainProgressPayload`

```json
{
  "job_id": "job_1",
  "decision": "accept",
  "stage": "execute",
  "summary": "已完成资料扫描，开始整理提纲。",
  "progress": 0.35,
  "next_step": "归类知识点"
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

#### `RightBrainResultPayload` (`decision=answer_only`)

```json
{
  "job_id": "job_2",
  "decision": "answer_only",
  "summary": "当前更适合直接给左脑理性答案素材。",
  "result_text": "更像是作息紊乱和压力叠加，先连续记录几天入睡与醒来时间会更有帮助。",
  "delivery_target": {
    "delivery_mode": "inline",
    "channel": "telegram",
    "chat_id": "123456"
  }
}
```

`right.event.result_ready` 正式承载两类完成态结果：

- `decision=accept`：右脑已执行完成或已产出执行结果
- `decision=answer_only`：右脑不执行后台任务，只给左脑回传理性答案素材

补充约束：

- `right.event.job_accepted` 表示 `audit_tool` 已返回“任务可以开始”，`DeepAgent` run 正式进入执行
- `right.event.progress` 表示执行中的关键往返；右脑应持续把关键进展回灌左脑，让用户可实时感知状态并随时停止
- `reject` 与 `answer_only` 是 `audit_tool` 直接发出的终止信号，不是 runtime 的二次裁决
- `RightBrainRuntime` 收到这类终止信号后，必须先把正式右脑事件回给左脑，再关停当前 run
- 右脑不承担“等待用户补充信息”的中间态；信息不足时，本次 run 直接结束，并通过 `reject` 或 `answer_only` 回左脑

### 10.3 右脑强约束

- 右脑负责后台理性与执行
- `RightBrainRuntime` 是常驻模块，只负责创建、管理、取消 `DeepAgent` run
- `audit_tool` 是 `DeepAgent` 内部审核钩子，不是外层独立流程引擎
- `audit_tool` 返回“任务可以开始”时，run 继续；返回 `reject / answer_only` 时，run 进入终止分支
- 右脑可以审查“能不能做、该不该做”
- 右脑不能直接越过左脑面向用户发言
- 右脑不做人机往返，不维护等待补充信息的中间态

### 10.4 `turn` 模式下右脑的正式工作模式

#### `skip`

- 不创建右脑作业
- 当前轮完全由左脑完成

#### `sync`

- 创建并等待一次右脑 `DeepAgent` run
- 左脑等待其结果
- 当前轮内给出统一回复
- `right.event.job_accepted`、`right.event.progress`、`right.event.job_rejected`、`right.event.result_ready(decision=accept|answer_only)` 都必须先回到左脑，再进入当前轮输出

#### `async`

- 创建同一种后台右脑 `DeepAgent` run
- 当前轮先结束
- 右脑后续继续把受理、进展、结果回流给左脑
- 用户可基于这些状态随时发起停止；真正的 run 生命周期由 `RightBrainRuntime` 统一控制
- 正式闭环为 `right.event.result_ready -> left.event.followup_ready -> output.event.push_ready`

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

`stream_id` 在输出模块中的正式语义如下：

- 当组合为 `turn + stream` 时，`stream_id` 是本次输出流的标识，由投递层生成，不要求复用输入侧流 ID
- 当组合为 `stream + stream` 时，如果外部通道要求单一双向流，可复用输入侧 `stream_id`；否则可由投递层生成独立输出流 ID，但必须保持同一会话关联

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

补充约束：

- `session/` 只保存原始流水，不等于正式 `memory`
- `session/<session_id>/front.jsonl` 保存 `用户 <-> 左脑` 原始记录
- `session/<session_id>/right.jsonl` 保存 `左脑 <-> 右脑` 原始记录
- 原始记录必须保留原始 `role`
- 原始记录必须支持多模态：`content` 作为纯文本主内容，`content_blocks` 作为原始多模态载荷
- `memory/short_term/` 保存短期记忆，可附带 `raw_messages`
- `memory/long_term/` 保存长期记忆正式事实源，可附带 `evidence_messages`
- 长期记忆中的原始证据至少要保留原始 `role`，并按场景保留原始 `content` / `content_blocks`
- `memory/vector/` 只保存向量索引，不是正式事实源
- 向量索引损坏或丢失时，必须可以从 `memory/long_term/` 重建
- `USER.md` / `SOUL.md` 只应是投影视图，不应充当正式记忆事实源

### 12.2 Reflection

必须只做：

- 轮后反思
- 深反思
- 人格与关系提炼

不得阻塞当前轮前台输出。

补充约束：

- 当右脑任务完成或取消时，`RightBrainRuntime` 只负责记录执行上下文摘要并触发反思
- 反思模块异步自行拉取所需上下文，不要求右脑同步打包全部材料
- 推荐记录项至少包括：近 `10` 轮摘要、短期/长期记忆引用、工具使用摘要、最终结果或取消原因

---

## 13. 代码文件正式落点

以下落点以包内逻辑路径表达正式归属。

在当前仓库中，这些逻辑路径实际对应 `emoticorebot/` 包前缀下的物理路径。

| 模块 | 正式逻辑落点 | 当前包内路径 | 说明 |
|------|------|------|------|
| `protocol` | `protocol/task_models.py` | `emoticorebot/protocol/task_models.py` | 唯一基础模型定义文件 |
| `protocol` | `protocol/events.py` | `emoticorebot/protocol/events.py` | 唯一事件 payload 文件 |
| `protocol` | `protocol/commands.py` | `emoticorebot/protocol/commands.py` | 唯一命令 payload 文件 |
| `protocol` | `protocol/topics.py` | `emoticorebot/protocol/topics.py` | 唯一 topic / event type 文件 |
| `session supervisor` | `session/runtime.py` | `emoticorebot/session/runtime.py` | `Session Supervisor` 核心承载文件 |
| `left brain` | `brain/executive.py` | `emoticorebot/brain/executive.py` | 左脑核心承载文件 |
| `right brain runtime` | `right/runtime.py` | `emoticorebot/right/runtime.py` | 右脑后台运行时核心承载文件 |
| `delivery plane` | `delivery/*` | `emoticorebot/delivery/service.py`、`emoticorebot/delivery/runtime.py` | 三类投递的统一投递层 |

---

## 14. 当前代码命名与正式语义

为避免歧义，正式语义如下：

| 当前代码名 | 所属模块 | 正式语义 |
|------|------|------|
| `ExecutiveBrain` | `brain/executive.py` | `Left Brain` |
| `RightBrainRuntime` | `right/runtime.py` | `Right Brain Runtime` |
| `DeepAgentExecutor` | `right/deep_agent_executor.py` | `Right Brain` 内部执行引擎 |
| `SessionRuntime` | `session/runtime.py` | `Session Supervisor` |
| `DeliveryService` | `delivery/service.py` | `Delivery Plane` |

这里是正式定性，不是临时解释。

---

## 15. `create_task` 的正式语义

`create_task` 在当前代码中仍是有效动作名，但正式语义不再是“必然执行一个任务”。

它被正式定义为：

> 向右脑提交一个待受理请求。

按模块理解如下：

| 模块 | `create_task` 的正式语义 |
|------|------|
| `left brain` | 左脑判断当前轮需要右脑参与，并构造右脑请求素材 |
| `session supervisor` | 调度层把该动作视为“向右脑提交待受理请求”，而不是直接承诺执行 |
| `right brain runtime` | 右脑立即启动一次 `DeepAgent` run；`audit_tool` 返回“任务可以开始”时继续执行，直接发出 `reject / answer_only` 终止信号时则回左脑并关停 |
| `delivery plane` | 只有当左脑基于右脑结果重新生成用户可见回复后，才进入正式投递 |

右脑对该请求拥有以下正式裁决权：

- `accept`
- `answer_only`
- `reject`

因此：

- 用户请求进入右脑，不等于右脑已经承诺执行
- 右脑 run 一旦启动，就由 `RightBrainRuntime` 统一管理生命周期
- `audit_tool` 只是审核钩子，不负责充当外层任务系统
- `reject / answer_only` 由 `audit_tool` 直接发终止信号，runtime 只负责通知左脑并关停

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
