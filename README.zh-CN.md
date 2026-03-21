# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个以陪伴为主线的个人 AI 助手，当前采用明确的 **单任务、按 batch 推进的 `Brain / Executor / World Model / Reflection`** 架构。

系统对外始终只有一个连续主体。`Brain` 负责理解用户、最终表达和下一批决策，`Executor` 负责异步执行与工具调用，`World Model` 与 `Reflection` 负责跨轮次延续任务与关系状态。

当前文档：

- [docs/brain-executor-architecture.zh-CN.md](docs/brain-executor-architecture.zh-CN.md)
- [docs/brain-executor-single-task-architecture.zh-CN.md](docs/brain-executor-single-task-architecture.zh-CN.md)
- [docs/brain-system-prompt.zh-CN.md](docs/brain-system-prompt.zh-CN.md)
- [docs/brain-executor-refactor-plan.zh-CN.md](docs/brain-executor-refactor-plan.zh-CN.md)

历史归档文档：

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
      "brainMode": {
        "model": "anthropic/claude-opus-4-5",
        "provider": "openrouter"
      },
      "executorMode": {
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

当前实现的主通路如下：

```text
Inbound Message
  -> TransportBus
  -> ConversationGateway
  -> RuntimeKernel
  -> PriorityPubSubBus
  -> InputNormalizer
  -> SessionRuntime
  -> BrainRuntime
  -> ExecutorRuntime (optional, current batch only)
  -> OutputRuntime
  -> DeliveryRuntime
  -> TransportBus
  -> ReflectionRuntime / ReflectionGovernor (async)
```

只有 `BrainRuntime` 可以决定最终回复。执行层只是内部执行链路，不是第二人格，也不直接对用户说话。

`TransportBus` 只是渠道 I/O 桥接层；真正的内部业务事件总线仍然只有 `PriorityPubSubBus`。

### 核心组件

| 组件 | 职责 |
|------|------|
| `BrainRuntime` | 解析 `#####user###### / #####Action######` 输出，生成最终对外表达，并决定是否发起执行或浅反思 |
| `ExecutorRuntime` | 承接当前批次执行，只在终态回填结果 |
| `ExecutorAgent` | 单次 batch 执行内核，负责工具调用与结果整理 |
| `RuntimeKernel` | 装配输入、会话、大脑、执行层、输出、投递与反思的事件闭环 |
| `PriorityPubSubBus` | 提供带优先级的 typed pub/sub，总线支持扇出与 interceptor |
| `SessionRuntime` | 负责会话内 turn/stream 调度、执行请求发起与 world model 驱动的主状态流转 |
| `OutputRuntime` | 将大脑事件统一收敛成 `inline / push / stream` 输出事件 |
| `DeliveryRuntime` | 唯一负责真正对外投递消息的组件 |
| `ReflectionGovernor` | 消费反思信号，治理长期记忆、人格与用户模型更新 |
| `WorldModelStore` | 持久化当前单任务运行态 |
| `ThreadStore` | 持久化对话历史与内部记录 |

### 事件闭环

1. 渠道输入先进入 `TransportBus`，再由 `ConversationGateway` 桥接成内部输入。
2. `InputNormalizer` 把原始输入统一成 `input.event.turn_received / stream_started / stream_chunk / stream_committed / stream_interrupted`。
3. `SessionRuntime` 先发出 `brain.command.reply_requested`。
4. `BrainRuntime` 在内部调用 LLM，输出 `#####user###### / #####Action######`，并发出 `brain.event.reply_ready / stream_delta_ready`。
5. 如果大脑决定启动执行层，`SessionRuntime` 再发出 `executor.command.job_requested`。
6. `ExecutorRuntime` 执行当前 batch，只在终态发出 `executor.event.job_rejected / result_ready`。
7. `SessionRuntime` 回填 `world model`，并且只有当前任务的终态结果才会再次唤醒 `BrainRuntime`。
8. `OutputRuntime` 把大脑结果统一收敛成 `output.event.inline_ready / push_ready / stream_*`。
9. `DeliveryRuntime` 完成真正投递；随后 `ReflectionRuntime / ReflectionGovernor` 异步处理反思与长期沉淀。

### 当前实现约束

- 同一 session 内只有一个 `current_task`
- 一轮最多一个 `execute`
- 并行只存在于 `current_task.current_checks`
- `Brain` 只在用户事件或当前批次终态后再决策
- `Executor` 只回填事实，不负责挑下一个任务
- `Reflection` 只由 `Brain` 触发

### 数据与持久化

| 层级 | 文件 / 目录 | 用途 |
|------|-------------|------|
| 对话历史 | `session/<session_id>/brain.jsonl` | `用户 <-> Brain` 原始记录 |
| 内部历史 | `session/<session_id>/executor.jsonl` | `Brain <-> Executor` 原始记录 |
| 认知事件 | `memory/cognitive_events.jsonl` | 短期认知事件流 |
| 长期记忆 | `memory/memory.jsonl` | 长期记忆唯一事实源 |
| 向量镜像 | `memory/vector/` | 长期记忆的检索镜像，而非事实源 |
| 世界模型 | `session/<session_id>/world_model.json` 或 store 后端 | 当前单任务运行态 |
| 工作区技能 | `skills/<name>/SKILL.md` | 工作流沉淀与执行提示 |

### 工具、执行与反思

- `ExecutorAgent` 当前负责执行层单次执行，但这只是执行内核实现细节，外围协议已经收敛到 `brain + executor + world model + runtime kernel`。
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
├── adapters/            # 会话网关与渠道适配
├── background/          # 潜意识守护、心跳任务、周期反思入口
├── bootstrap.py         # RuntimeHost，系统装配宿主
├── brain/               # 大脑提示词、包解析与决策运行时
├── bus/                 # 优先级事件总线与路由
├── channels/            # Telegram / Discord / Slack 等渠道实现
├── cli/                 # 命令行入口
├── config/              # 配置加载与 schema
├── context/             # 大脑上下文组装
├── cron/                # 定时任务服务
├── delivery/            # 最终消息投递
├── executor/            # batch 执行运行时与工具执行代理
├── input/               # 输入归一化
├── memory/              # 长期记忆存储、检索与向量镜像
├── models/              # 共享运行时模型
├── output/              # 输出事件收敛
├── protocol/            # typed commands / events / topics
├── providers/           # LLM 与转写 provider
├── reflection/          # 浅反思 / 深反思 / 治理器
├── runtime/             # RuntimeKernel 与 TransportBus
├── session/             # session 流转、线程存储、world model 唤醒
├── skills/              # 内置工作流技能
├── templates/           # 工作区初始化模板
├── tools/               # 内置工具与 MCP 注册
├── utils/               # 通用辅助函数
└── world_model/         # 单任务 schema、reducers 与持久化
```

---

## 设计结论

当前版本的核心一句话是：

`BrainRuntime 按 batch 决策与最终表达，ExecutorRuntime 按 batch 执行，SessionRuntime + World Model 负责主状态流转，ReflectionGovernor 负责长期沉淀。`

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。
