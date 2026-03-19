# 陪伴机器人统一架构：左脑 / 右脑、双输入、三投递（当前版）

## 1. 核心定义

这个系统不是多个对外角色，而是一个统一主体，内部拆成：

- `left brain`：前台表达与决策
- `right brain`：后台审核与执行
- `session`：会话调度与转发
- `output`：最终投递
- `reflection`：事后反思与长期更新

用户始终只面对一个主体。

---

## 2. 两类输入、三类投递

### 2.1 两类输入

- `turn`
  - 一次完整输入形成一次正式判断
- `stream`
  - 输入流通过 `start/chunk/commit/interrupt` 驱动

### 2.2 三类投递

- `inline`
  - 当前链路里完整回复
- `push`
  - 当前链路结束，之后主动通知
- `stream`
  - 当前链路里分段输出

### 2.3 关键点

输入类型和投递类型不是一一绑定的。

例如：

- `turn + inline`：普通聊天
- `turn + stream`：SSE
- `turn + push`：单轮触发后台处理
- `stream + stream`：电话 / 实时语音
- `stream + push`：电话里发起长任务，挂断后通知
- `stream + inline`：当前流式会话内的单次最终收束

---

## 3. 入口原则

入口只负责携带事实，不负责替左脑做任务模式判断。

当前实现里，入口通过输入 `metadata` 带入：

- `source_input_mode`
- `current_delivery_mode`
- `available_delivery_modes`

示例：

- 普通文字 turn：
  - `source_input_mode=turn`
  - `current_delivery_mode=inline`
  - `available_delivery_modes=[inline, push]`
- 电话 stream：
  - `source_input_mode=stream`
  - `current_delivery_mode=stream`
  - `available_delivery_modes=[stream, inline, push]`
- SSE turn：
  - `source_input_mode=turn`
  - `current_delivery_mode=stream`
  - `available_delivery_modes=[stream, push]`

这一步只提供环境事实。

---

## 4. 左脑协议

左脑是唯一前台主体，必须自己决定：

- 给用户说什么
- 是否要启右脑
- 如果启右脑，是当前会话继续收束，还是当前轮结束后再通知

左脑 LLM 必须输出：

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
- 不再输出旧策略字段
- 不再输出 `mode=...`

### 4.1 `task_mode` 的真正含义

- `skip`
  - 不启右脑
- `sync`
  - 启右脑
  - 结果仍沿当前会话链路收束
- `async`
  - 启右脑
  - 当前轮先结束
  - 结果后续走 `push`

这里的重点不是“阻塞等待”，而是“结果收束到哪里”。

---

## 5. 为什么不能再硬编码

不能再写这种规则：

- `stream -> inline`
- `turn -> push`

原因很简单：

- 电话场景下，用户可能在通话中发起一个长任务，然后挂断，最终应走 `push`
- 文字场景下，也可能是 `turn + stream`（例如 SSE）
- 是否当前收束，必须由左脑结合语义和环境判断，而不是 runtime 只看输入类型就下注

所以：

- `turn/stream` 是输入事实
- `inline/push/stream` 是投递事实
- `skip/sync/async` 是左脑裁决

三者必须分层。

---

## 6. 运行链路

### 6.1 用户输入

`input.event.* -> session -> left.command.reply_requested -> left.event.reply_ready -> output.event.*`

### 6.2 右脑任务

`left.event.reply_ready(invoke_right_brain=true) -> session -> right.command.job_requested -> right.event.* -> session -> left.command.reply_requested(followup_context) -> left.event.followup_ready -> output.event.*`

### 6.3 各层职责

- `input`
  - 只标准化输入并补齐环境默认值
- `session`
  - 只维护会话态并转发
- `left`
  - 只做表达和 `task_mode` 判定
- `right`
  - 只做审核与执行
- `output`
  - 只做最终投递
- `delivery`
  - 只把 output 结果送到 transport，并回写送达状态
- `reflection`
  - 只做事后反思与长期更新
- `memory`
  - 只做长期记忆存储与读侧检索

---

## 7. 当前实现里的收敛方式

### 7.1 当前轮回复

当前轮回复使用 `current_delivery_mode`：

- `inline` -> 当前轮完整回复
- `stream` -> 当前轮流式输出
- `push` -> 当前轮直接进入后续推送链路

### 7.2 右脑 followup

- `task_mode=sync`
  - followup 目标沿当前前台模式收束
  - 当前实现里：
    - 当前前台为 `inline` -> followup 走 `inline`
    - 当前前台为 `stream` -> followup 走 `stream`
- `task_mode=async`
  - followup 固定走 `push`

### 7.3 中间进展

- `accepted / progress` 在 `push` 场景下可以正常投递
- 在非 `push` 场景下，通常只保留最终结果，中间进展可 suppress

---

## 8. 右脑原则

右脑不是第二人格，只是后台执行系统。

右脑只做：

- 审核是否能做
- 调工具 / 执行
- 回流 `accepted / progress / rejected / result`

右脑不做：

- 直接跟用户说话
- 等用户补资料
- 自己猜 `delivery_target`

现在 `delivery_target` 必须由上游显式给出。

---

## 9. 当前代码落点

- 输入标准化：`emoticorebot/input/normalizer.py`
- Session：`emoticorebot/session/runtime.py`
- Left：`emoticorebot/left_brain/packet.py`、`emoticorebot/left_brain/runtime.py`
- Right：`emoticorebot/right_brain/runtime.py`、`emoticorebot/right_brain/store.py`
- Output：`emoticorebot/output/runtime.py`
- Delivery：`emoticorebot/delivery/runtime.py`、`emoticorebot/delivery/service.py`
- Reflection：`emoticorebot/reflection/runtime.py`、`emoticorebot/reflection/governor.py`
- Memory：`emoticorebot/memory/store.py`、`emoticorebot/memory/retrieval.py`
- Kernel：`emoticorebot/runtime/kernel.py`

---

## 10. 当前架构的一句话

入口给环境事实，左脑出 `task_mode`，session 转发，右脑执行，output 成形，delivery 送达，reflection 事后更新 memory。

