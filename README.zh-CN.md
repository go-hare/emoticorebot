# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个以陪伴为主线的个人 AI 助手，当前采用明确的 **`Left Brain + Right Brain + RuntimeKernel`** 架构。

- `LeftBrainRuntime` 是唯一对外主体，负责理解用户、做出决策并完成最终表达
- `RightBrainRuntime` 负责任性执行、工具调用、审核钩子与结果回流
- `RuntimeKernel` 负责事件闭环、投递整合与反思调度
- `DeliveryRuntime / ReflectionGovernor / ReflectionRuntime` 在总线上闭环输出投递与长期沉淀

详细文档：

- [docs/companion-protocol-spec.zh-CN.md](docs/companion-protocol-spec.zh-CN.md)
- [docs/companion-left-right-brain-architecture.zh-CN.md](docs/companion-left-right-brain-architecture.zh-CN.md)
- [docs/companion-left-right-brain-module-contracts.zh-CN.md](docs/companion-left-right-brain-module-contracts.zh-CN.md)

---

## 安装

从源码安装：

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

---

## 快速开始

1. 初始化本地工作区：

```bash
emoticorebot onboard
```

2. 编辑 `~/.emoticorebot/config.json`：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "leftBrainMode": {
        "model": "anthropic/claude-opus-4-5",
        "provider": "openrouter"
      },
      "rightBrainMode": {
        "model": "anthropic/claude-opus-4-5",
        "provider": "openrouter"
      }
    }
  }
}
```

3. 启动 CLI 对话：

```bash
emoticorebot agent
```

4. 启动聊天网关：

```bash
emoticorebot gateway
```

当前 gateway 接入的渠道都使用 WebSocket 或轮询模式，不需要本地监听端口。`gateway.port` 目前保留给未来的 webhook 型渠道。

---

## 架构

当前 v3 的主通路如下：

```text
Inbound Message
  -> TransportBus
  -> ConversationGateway
  -> RuntimeKernel
  -> PriorityPubSubBus
  -> InputNormalizer
  -> SessionRuntime
  -> LeftBrainRuntime
  -> RightBrainRuntime (optional)
  -> OutputRuntime
  -> DeliveryRuntime
  -> TransportBus
  -> ReflectionRuntime / ReflectionGovernor (async)
```

只有 `LeftBrainRuntime` 可以决定最终回复。右脑只是内部执行链路，不是第二人格。

`TransportBus` 只是渠道 I/O 桥接层；真正的内部业务事件总线仍然只有 `PriorityPubSubBus`。

### 核心组件

| 组件 | 职责 |
|------|------|
| `LeftBrainRuntime` | 收敛 `right_brain_strategy`，解析内部 `####task#### / ####user####` 输出，并生成最终对外表达 |
| `RightBrainRuntime` | 承接右脑执行 run，管理受理 / 进展 / 结果 / 取消的生命周期 |
| `RightBrainExecutor` | 单次执行内核，负责审核钩子、工具调用与结果整理 |
| `RuntimeKernel` | 装配输入、会话、左脑、右脑、输出、投递与反思的事件闭环 |
| `PriorityPubSubBus` | 提供带优先级的 typed pub/sub，总线支持扇出与 interceptor |
| `SessionRuntime` | 负责会话内 turn/stream 调度、右脑请求发起与状态视图 |
| `OutputRuntime` | 将左脑事件统一收敛成 `inline / push / stream` 输出事件 |
| `DeliveryRuntime` | 唯一负责真正对外投递消息的组件 |
| `ReflectionGovernor` | 消费反思信号，治理长期记忆、人格与用户模型更新 |
| `ThreadStore` | 持久化对话历史与内部记录 |

### 事件闭环

1. 渠道输入先进入 `TransportBus`，再由 `ConversationGateway` 桥接成内部输入。
2. `InputNormalizer` 把原始输入统一成 `input.event.turn_received / stream_started / stream_chunk / stream_committed / stream_interrupted`。
3. `SessionRuntime` 先发出 `left.command.reply_requested`。
4. `LeftBrainRuntime` 在内部调用 LLM，输出 `####task#### / ####user####`，并发出 `left.event.reply_ready / followup_ready / stream_delta_ready`。
5. 如果左脑决定启动右脑，`SessionRuntime` 再发出 `right.command.job_requested`。
6. `RightBrainRuntime` 执行一次右脑 run，并发出 `right.event.job_accepted / progress / job_rejected / result_ready`。
7. `OutputRuntime` 把左脑结果统一收敛成 `output.event.inline_ready / push_ready / stream_*`。
8. `DeliveryRuntime` 完成真正投递；随后 `ReflectionRuntime / ReflectionGovernor` 异步处理反思与长期沉淀。

### 数据与持久化

| 层级 | 文件 / 目录 | 用途 |
|------|-------------|------|
| 对话历史 | `session/<session_id>/left.jsonl` | `用户 <-> 左脑` 原始记录 |
| 内部历史 | `session/<session_id>/right.jsonl` | `左脑 <-> 右脑` 原始记录 |
| 短期记忆 | `memory/short_term/` | 会话级工作态与轮摘要 |
| 长期记忆 | `memory/long_term/memory.jsonl` | 长期记忆唯一事实源 |
| 向量镜像 | `memory/vector/` | 长期记忆的检索镜像，而非事实源 |
| 工作区技能 | `skills/<name>/SKILL.md` | 工作流沉淀与执行提示 |

### 工具、执行与反思

- `RightBrainExecutor` 当前负责右脑单次执行，但这只是执行内核实现细节，外围协议已经收敛到 `left_brain + right_brain + runtime kernel`。
- 执行层可组合工具注册表、MCP 工具、工作区 `skills/` 与内置技能。
- `SubconsciousDaemon` 继续负责 PAD 衰减、周期性反思和主动消息机会检测。
- `ReflectionGovernor` 与反思层只消费规范化输入，不依赖旧任务系统旁路事件。

---

## 常用命令

```bash
emoticorebot onboard
emoticorebot agent
emoticorebot gateway
emoticorebot status
emoticorebot cron list
emoticorebot channels status
```

---

## 项目结构

```text
emoticorebot/
├── adapters/            # 渠道入口、会话网关、出站分发
├── background/          # 潜意识守护、周期反思、心跳任务
├── bootstrap.py         # RuntimeHost，系统装配宿主
├── left/
│   ├── context.py
│   ├── packet.py
│   ├── reply_policy.py
│   └── runtime.py
├── right/
│   ├── backend.py
│   ├── executor.py
│   ├── hooks.py
│   ├── runtime.py
│   ├── skills.py
│   ├── state.py
│   ├── store.py
│   └── trace.py
├── bus/                 # 优先级总线、路由、订阅、interceptor
├── delivery/
│   ├── runtime.py
│   └── service.py
├── memory/
│   ├── crystallizer.py
│   ├── retrieval.py
│   ├── short_term.py
│   ├── store.py
│   └── vector_index.py
├── protocol/            # envelope / commands / events / models
│   ├── commands.py
│   ├── contracts.py
│   ├── envelope.py
│   ├── event_contracts.py
│   ├── events.py
│   ├── reflection_models.py
│   ├── priorities.py
│   ├── task_models.py
│   └── topics.py
├── runtime/
│   ├── kernel.py
│   └── transport_bus.py
├── safety/
│   └── guard.py
├── session/
│   ├── models.py
│   ├── runtime.py
│   ├── history_store.py
│   └── thread_store.py
├── channels/
├── tools/
│   ├── manager.py
│   └── mcp.py
├── config/
├── cron/
├── models/
├── providers/
│   └── factory.py
├── skills/
├── templates/
├── reflection/
│   ├── candidates.py
│   ├── cognitive.py
│   ├── deep.py
│   ├── governor.py
│   ├── input.py
│   ├── manager.py
│   ├── persona.py
│   ├── runtime.py
│   └── turn.py
├── utils/
└── tests/
```

---

## 设计结论

当前版本的核心一句话是：

`LeftBrainRuntime 负责决策与最终表达，RuntimeKernel 负责事件闭环，ThreadStore / ReflectionGovernor 负责沉淀。`

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。


