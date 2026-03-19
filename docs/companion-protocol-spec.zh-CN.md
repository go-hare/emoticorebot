# Companion Protocol Spec（当前版）

## 1. 协议原则

- 只有两类输入：`turn`、`stream`
- 只有三类投递：`inline`、`push`、`stream`
- 入口负责携带环境事实，不负责替左脑做同步/异步裁决
- 左脑负责两类输出：
  - 给用户看的文本
  - 任务动作与任务模式
- 左脑输出协议只保留当前字段集

---

## 2. 输入与投递

### 2.1 输入

| 输入 | 含义 |
|------|------|
| `turn` | 单轮输入，用户提交后处理 |
| `stream` | 连续输入流，按 `start/chunk/commit/interrupt` 驱动 |

### 2.2 投递

| 投递 | 含义 |
|------|------|
| `inline` | 当前链路里一次性给完整结果 |
| `push` | 当前链路先结束，稍后主动通知 |
| `stream` | 当前链路里分段输出 |

### 2.3 常见组合

| 输入 | 投递 | 典型场景 |
|------|------|----------|
| `turn` | `inline` | 普通文字问答 |
| `turn` | `push` | 单轮触发后台处理，稍后通知 |
| `turn` | `stream` | SSE 文本流 |
| `stream` | `stream` | 电话 / 实时语音 |
| `stream` | `push` | 通话中发起长任务，挂断后通知 |
| `stream` | `inline` | 当前流式会话内的最终收束 |

---

## 3. 入口环境事实

当前实现把环境事实放在输入 `metadata` 中，由入口带入。

关键字段：

- `source_input_mode`: `turn | stream`
- `current_delivery_mode`: `inline | push | stream`
- `available_delivery_modes`: `list[inline|push|stream]`

约束：

- `source_input_mode` 是输入事实
- `current_delivery_mode` 是当前这轮前台链路的投递事实
- `available_delivery_modes` 表示当前入口后续还能支持哪些投递
- 左脑必须基于这些事实自行决定 `task_mode`
- runtime 不再写死 `turn -> push`、`stream -> inline` 这类裁决

默认值：

- `turn` 输入默认：`current_delivery_mode=inline`，`available_delivery_modes=[inline, push]`
- `stream` 输入默认：`current_delivery_mode=stream`，`available_delivery_modes=[stream, inline, push]`
- 如果入口是 `turn + stream`（例如 SSE），入口应显式覆盖这组默认值

---

## 4. 左脑内部输出协议

左脑处理用户轮次时，LLM 必须输出且只能输出两个区块：

```text
####user####
<给用户看的自然语言回复>

####task####
action=<none|create_task|cancel_task>
task_mode=<skip|sync|async>
task_id=<仅 cancel_task 时填写>
reason=<可选>
```

约束：

- 必须先输出 `####user####`，再输出 `####task####`
- `####user####` 可能直接流式发给用户
- `####task####` 只给系统解析
- `action=none` 时，`task_mode` 必须是 `skip`
- `action=create_task|cancel_task` 时，`task_mode` 必须是 `sync` 或 `async`
- 不允许再输出任何旧兼容字段

### 4.1 `task_mode` 语义

- `skip`
  - 不启动右脑
- `sync`
  - 启动右脑
  - 右脑回流仍沿当前会话链路收束
  - 如果当前前台投递是 `stream`，followup 也走 `stream`
  - 如果当前前台投递是 `inline`，followup 走 `inline`
- `async`
  - 启动右脑
  - 当前轮先结束
  - 右脑结果后续走 `push`

---

## 5. 左脑对外协议

### 5.1 `LeftReplyRequestPayload`

左脑统一入口，只允许二选一：

- `turn_input`
- `followup_context`

### 5.2 `LeftReplyReadyPayload`

关键字段：

- `reply_text`
- `reply_kind=answer|status`
- `delivery_target`
- `invoke_right_brain`
- `right_brain_request`
- `stream_id / stream_state`（当前轮为 `stream` 时使用）
- `metadata`

规则：

- 当前轮回复的 `delivery_target` 直接来自入口环境里的 `current_delivery_mode`
- 当前轮若是 `stream`，左脑会拆 `####user####` 做流式输出
- 即使模型本身没走 token stream，只要当前轮目标是 `stream`，output 也会以 `stream_close` 收束

### 5.3 `LeftStreamDeltaPayload`

- 只承载 `####user####` 的流式增量
- `####task####` 绝不能进入用户可见流

### 5.4 `LeftFollowupReadyPayload`

关键字段：

- `job_id`
- `source_event`
- `source_decision`
- `reply_text`
- `reply_kind`
- `delivery_target`

规则：

- `sync` followup 的 `delivery_target` 取当前会话前台模式
- `async` followup 的 `delivery_target` 固定为 `push`
- `accepted / progress` 这类中间 followup 在非 `push` 场景下可被 `suppress_output`
- `stream` followup 当前实现使用单个 `stream_close` 事件做收束

---

## 6. Session 与 Right 链路

### 6.1 主链路

用户输入：

`input.event.* -> session -> left.command.reply_requested -> left.event.reply_ready -> output.event.*`

右脑任务：

`left.event.reply_ready(invoke_right_brain=true) -> session -> right.command.job_requested -> right.event.* -> session -> left.command.reply_requested(followup_context) -> left.event.followup_ready -> output.event.*`

### 6.2 `RightBrainJobRequestPayload`

关键字段：

- `job_id`
- `job_action=create_task|cancel_task`
- `source_text`
- `request_text`
- `delivery_target`
- `context`

规则：

- `delivery_target` 现在是必须的运行时事实
- 右脑 runtime 不再自己 fallback 推断 delivery mode
- `request_text` 直接使用用户原文或取消原因

### 6.3 `RightBrain*Payload`

关键字段：

- `accepted / progress / rejected / result`
- `delivery_target`

规则：

- 右脑四类回流事件都必须显式携带 `delivery_target`
- session / left 不再从 task store 回查投递目标
- 也不再默认补 `push`

---

## 7. Output 协议

输出层只做投递，不再替左脑改判任务模式。

规则：

- 有 `stream_state` 时，发 `output.event.stream_*`
- `delivery_target.delivery_mode=push` 时，发 `output.event.push_ready`
- 其余发 `output.event.inline_ready`
- followup 若目标是 `stream`，当前实现发单个 `output.event.stream_close`

---

## 8. Delivery / Reflection / Memory 协议

### 8.1 Delivery

`delivery` 只消费 output 结果，不参与 left/right 裁决。

规则：

- `output.event.*` 成功投递后，发布 `output.event.replied`
- transport 不可用或路由缺失时，发布 `output.event.delivery_failed`
- `suppress_delivery=true` 时，不走 transport，但仍发布 `output.event.replied`
- stale stream 被丢弃时，当前实现只向 transport 发一个 `superseded` 收束包

### 8.2 Reflection

reflection 相关正式共享协议：

- `REFLECTION_LIGHT`
- `REFLECTION_DEEP`
- `REFLECTION_WRITE_REQUEST`
- `REFLECTION_WRITE_COMMITTED`
- `REFLECTION_UPDATE_PERSONA`
- `REFLECTION_UPDATE_USER_MODEL`

规则：

- `reflection/runtime.py` 只负责 periodic deep reflection timer
- `reflection/governor.py` 负责消费反思信号、写入记忆、发布更新事件
- turn/deep reflection 都是事后链路，不参与当前轮回复裁决
- `ReflectionWriteRequestPayload.memory_type` 使用请求侧类型：
  - `persona`
  - `user_model`
  - `episodic`
  - `task_experience`
  - `tool_experience`

### 8.3 Memory

memory 当前正式存储面：

- 长期记忆文件：`memory/long_term/memory.jsonl`
- schema：`memory.long_term.v1`
- 正式存储 `memory_type`：
  - `relationship`
  - `fact`
  - `working`
  - `execution`
  - `reflection`

规则：

- reflection 写入请求类型会先映射，再落入正式存储类型
- 左脑读侧通过 `MemoryRetrieval`
- `MemoryStore` 是 append-only source of truth

---

## 9. 强约束

- 左脑永远是唯一对用户说话的主体
- 右脑永远不能直接面向用户发言
- 右脑当前 run 不支持等待用户补充信息；信息不足直接 `reject`
- `audit_tool(decision="accept")` 之前不能发 `progress / result`
- `reject / answer_only` 是右脑直接终止信号，不是 runtime 二次裁决
- 输入层不拆 `####user#### / ####task####`
- 同步/异步裁决必须由左脑 LLM 输出 `task_mode` 给出

---

## 10. 当前实现对应文件

- 输入标准化：`emoticorebot/input/normalizer.py`
- Session 汇总：`emoticorebot/session/runtime.py`
- 左脑解析与流式拆分：`emoticorebot/left_brain/packet.py`、`emoticorebot/left_brain/runtime.py`
- 右脑运行时：`emoticorebot/right_brain/runtime.py`、`emoticorebot/right_brain/store.py`
- 输出层：`emoticorebot/output/runtime.py`
- 送达层：`emoticorebot/delivery/runtime.py`、`emoticorebot/delivery/service.py`
- 反思层：`emoticorebot/reflection/runtime.py`、`emoticorebot/reflection/governor.py`
- 记忆层：`emoticorebot/memory/store.py`、`emoticorebot/memory/retrieval.py`
- Kernel 入口：`emoticorebot/runtime/kernel.py`

