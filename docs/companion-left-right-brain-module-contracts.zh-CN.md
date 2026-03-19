# Companion Left/Right Brain Module Contracts（当前版）

## 1. 总体边界

- `left` 负责前台表达、动作判定、`task_mode` 判定
- `session` 负责会话态和左右脑转发
- `right` 负责后台审核、执行、结果回流
- `output` 负责把左脑结果投递为 `inline / push / stream`
- `delivery` 负责把 output 结果送到 transport，并回写送达状态
- `reflection` 负责事后反思和长期更新
- `memory` 负责长期记忆存储与读侧检索

---

## 2. `left` 模块契约

文件：

- `emoticorebot/left_brain/packet.py`
- `emoticorebot/left_brain/runtime.py`
- `emoticorebot/left_brain/reply_policy.py`

### 2.1 职责

- 调用左脑 LLM
- 解析 `####user#### / ####task####`
- 把 `####user####` 做当前轮流式拆分
- 构造当前轮 `delivery_target`
- 根据 `action + task_mode` 构造右脑请求
- 将右脑 followup 回流重新表达成统一主体口吻

### 2.2 左脑只决定什么

- 用户可见文本
- `action=<none|create_task|cancel_task>`
- `task_mode=<skip|sync|async>`

### 2.3 左脑不再决定什么

- 旧兼容协议字段
- 输入层拆分

### 2.4 内部输出契约

```text
####user####
<给用户看的自然语言回复>

####task####
action=<none|create_task|cancel_task>
task_mode=<skip|sync|async>
task_id=<仅 cancel_task 时填写>
reason=<可选>
```

规则：

- `action=none -> task_mode=skip`
- `create_task/cancel_task -> task_mode=sync|async`
- `####task####` 只允许紧凑 `key=value` 行

### 2.5 左脑如何使用入口环境

左脑读取输入 `metadata` 中的这些事实：

- `source_input_mode`
- `current_delivery_mode`
- `available_delivery_modes`

左脑必须自己判断：

- 当前请求是否只是直接回答
- 是否需要右脑
- 如果需要右脑，是沿当前会话收束，还是当前结束后再通知

---

## 3. `session` 模块契约

文件：

- `emoticorebot/session/runtime.py`
- `emoticorebot/session/models.py`

### 3.1 职责

- 订阅输入事件
- 维护 session 的活跃输入流、活跃回复流、任务视图、trace cursor
- 把用户输入和右脑回流统一收敛成 `left.command.reply_requested`
- 当左脑要求启动右脑时，发出 `right.command.job_requested`

### 3.2 不做的事

- 不做 LLM 决策
- 不做用户表达
- 不做同步/异步裁决
- 不再读取任何旧兼容策略字段

---

## 4. `right` 模块契约

文件：

- `emoticorebot/right_brain/runtime.py`
- `emoticorebot/right_brain/store.py`
- `emoticorebot/right_brain/executor.py`
- `emoticorebot/right_brain/hooks.py`
- `emoticorebot/right_brain/state.py`

### 4.1 职责

- 接受 `right.command.job_requested`
- 创建或取消右脑 run
- 严格执行 `accept -> progress/result` 顺序
- 发布 `accepted / progress / rejected / result`
- 保存最小任务快照和 trace

### 4.2 强约束

- 不能直接面向用户发言
- 不支持等待用户补充信息
- `delivery_target` 必须由上游显式给出
- 不再自己 fallback 推断 delivery mode
- `accepted / progress / rejected / result` 四类事件也必须显式带上 `delivery_target`
- `reject / answer_only` 直接终止本次 run

### 4.3 `task_mode` 到右脑投递的收敛

当前实现中：

- `task_mode=sync` -> 右脑结果沿当前会话前台模式回流
- `task_mode=async` -> 右脑结果回流目标固定为 `push`

---

## 5. `output` 模块契约

文件：

- `emoticorebot/output/runtime.py`
- `emoticorebot/output/builder.py`

### 5.1 职责

- 将左脑结果转成：
  - `output.event.inline_ready`
  - `output.event.push_ready`
  - `output.event.stream_open/delta/close`
- 对回复做 safety guard
- 处理 `suppress_output`

### 5.2 当前轮回复规则

- `delivery_target.delivery_mode=inline` -> `inline`
- `delivery_target.delivery_mode=push` -> `push`
- `delivery_target.delivery_mode=stream` -> `stream`
- 如果当前轮目标是 `stream`，但左脑本轮没有实际 token 流，output 仍会发一个 `stream_close` 完成收束

### 5.3 followup 规则

- `push` followup -> `push`
- `inline` followup -> `inline`
- `stream` followup -> 当前实现发一个 `stream_close` 单包收束
- `accepted / progress` 在非 `push` 场景下可被 suppress

---

## 6. `delivery` 模块契约

文件：

- `emoticorebot/delivery/runtime.py`
- `emoticorebot/delivery/service.py`

### 6.1 职责

- 订阅 `output.event.inline_ready / push_ready / stream_*`
- 将 output 层已经确定好的结果发送给 transport
- 成功后发布 `output.event.replied`
- 失败时发布 `output.event.delivery_failed`

### 6.2 强约束

- 不再改判 `task_mode`
- 不再猜 `delivery_target`
- 不再改写 reply 文本语义
- stale stream 只发送 `superseded` 收束，不补正常完成态
- `suppress_delivery` 时不走 transport，但仍发布可观测的 `output.event.replied`

---

## 7. `reflection` 模块契约

文件：

- `emoticorebot/reflection/runtime.py`
- `emoticorebot/reflection/governor.py`
- `emoticorebot/reflection/manager.py`
- `emoticorebot/reflection/persona.py`

### 7.1 职责

- 接受 `REFLECTION_LIGHT / REFLECTION_DEEP`
- 执行 turn reflection 与 deep reflection
- 接受 `REFLECTION_WRITE_REQUEST`
- 持久化记忆并发布 `REFLECTION_WRITE_COMMITTED`
- 当人格或用户模型被治理更新时，发布 `REFLECTION_UPDATE_PERSONA / REFLECTION_UPDATE_USER_MODEL`

### 7.2 强约束

- reflection 只做事后反思与长期更新，不参与当前轮用户表达
- periodic deep reflection 定时器只由 `reflection/runtime.py` 持有
- 反思触发去重由 governor 负责，不由 left/right/session 自己处理
- `ReflectionWriteRequestPayload.memory_type` 是反思写入请求类型，不等同于最终落盘 `memory_type`

---

## 8. `memory` 模块契约

文件：

- `emoticorebot/memory/store.py`
- `emoticorebot/memory/retrieval.py`

### 8.1 职责

- `MemoryStore` 作为长期记忆 append-only source of truth
- `MemoryRetrieval` 作为左脑读侧 facade
- 为左脑构造长期记忆上下文
- 为右脑/任务执行构造 task memory bundle

### 8.2 当前正式存储面

- 长期记忆文件：`memory/long_term/memory.jsonl`
- 当前落盘 schema：`memory.long_term.v1`
- 正式存储 `memory_type`：
  - `relationship`
  - `fact`
  - `working`
  - `execution`
  - `reflection`

### 8.3 强约束

- 左脑通过 `MemoryRetrieval` 读取，不直接耦合 `MemoryStore`
- reflection 请求类型会先映射，再落入正式存储类型
- memory 模块本身不决定人格治理，只提供存储与检索能力

---

## 9. 当前正式共享模型

### 9.1 `protocol/contracts.py`

保留：

- `InputMode`
- `DeliveryMode`
- `RightBrainJobAction`
- `RightBrainDecision`
- `TaskMode`

### 9.2 `protocol/events.py`

重点模型：

- `TurnInputPayload`
- `StreamStartPayload`
- `StreamChunkPayload`
- `StreamCommitPayload`
- `StreamInterruptedPayload`
- `LeftReplyReadyPayload`
- `LeftStreamDeltaPayload`
- `LeftFollowupReadyPayload`
- `RightBrainAcceptedPayload`
- `RightBrainProgressPayload`
- `RightBrainRejectedPayload`
- `RightBrainResultPayload`
- `Output*Payload`
- `SystemSignalPayload`

### 9.3 `protocol/commands.py`

重点模型：

- `LeftReplyRequestPayload`
- `FollowupContextPayload`
- `RightBrainJobRequestPayload`

### 9.4 `protocol/reflection_models.py`

重点模型：

- `ReflectionSignalPayload`
- `ReflectionWriteRequestPayload`
- `ReflectionWriteCommittedPayload`
- `ReflectionUpdatePayload`

---

## 10. 已删除或已废弃的旧概念

- 旧兼容策略字段
- 旧等待类状态
- 旧多模式回复字段
- 旧 `front/` 命名
- 旧 `brain/` 命名
- 旧通用 task 包装层

---

## 11. 一句话理解

- 入口给左脑环境事实
- 左脑输出用户文本和 `task_mode`
- session 只转发
- 右脑只执行
- output 只投递
- delivery 只送达
- reflection 只反思
- memory 只存取

