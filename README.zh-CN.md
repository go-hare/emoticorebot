# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个以陪伴为主线的个人 AI 助手，当前采用明确的 **`ExecutiveBrain + RuntimeKernel + AgentTeam`** 架构。

- `ExecutiveBrain` 是唯一对外主体，负责理解用户、做出决策并完成最终表达
- `RuntimeKernel` 负责任务生命周期、状态机、恢复、调度与事件闭环
- `AgentTeam` 由 `planner / worker / reviewer` 组成，负责内部任务执行
- `SafetyGuard / DeliveryService / MemoryGovernor` 在总线上闭环输出安全、投递与反思沉淀

详细文档：

- [docs/final-brain-runtime-architecture.zh-CN.md](docs/final-brain-runtime-architecture.zh-CN.md)

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
      "workerMode": {
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

---

## 架构

当前 v3 的主通路如下：

```text
Inbound Message
  -> TransportBus
  -> ConversationGateway
  -> RuntimeKernel
  -> PriorityPubSubBus
  -> ExecutiveBrain
  -> RuntimeService / TaskStore
  -> Planner / Worker / Reviewer
  -> SafetyGuard
  -> DeliveryService
  -> TransportBus
  -> ReflectionCoordinator / MemoryGovernor (async)
```

只有 `ExecutiveBrain` 可以决定最终回复。`planner / worker / reviewer` 都只是内部执行角色，不是第二人格。

`TransportBus` 只是渠道 I/O 桥接层；真正的内部业务事件总线仍然只有 `PriorityPubSubBus`。

### 核心组件

| 组件 | 职责 |
|------|------|
| `ExecutiveBrain` | 判断本轮是直接回复、追问、创建任务、恢复任务还是取消任务，并生成最终对外表达 |
| `RuntimeKernel` | 持有任务状态机、任务表、分配策略、恢复逻辑与调度入口 |
| `PriorityPubSubBus` | 提供带优先级的 typed pub/sub，总线支持扇出与 interceptor |
| `AgentTeam` | 注册 `planner / worker / reviewer` 三类执行角色 |
| `SafetyGuard` | 在真正投递前审核 `reply draft` 和敏感结果，执行 allow / redact / block |
| `DeliveryService` | 唯一负责真正对外投递消息的组件 |
| `MemoryGovernor` | 消费反思信号，治理长期记忆、人格与用户模型更新 |
| `ThreadStore` | 持久化对话历史与内部记录 |

### 事件闭环

1. 渠道输入先进入 `TransportBus`，再由 `ConversationGateway` 桥接成内部 `input.event.user_message`。
2. `ExecutiveBrain` 发布 `brain.command.reply`、`brain.command.ask_user`、`brain.command.create_task` 或 `brain.command.resume_task`。
3. `RuntimeKernel` 持久化任务并发布 `runtime.command.assign_agent` / `runtime.command.resume_agent`。
4. `planner / worker / reviewer` 发布 `task.report.*`，runtime 再归一化为 `task.event.*`。
5. `RuntimeService` 把 `brain.command.reply` / `brain.command.ask_user` 转成 `output.event.reply_ready`。
6. `SafetyGuard` 审核后发布 `output.event.reply_approved`、`output.event.reply_redacted` 或 `output.event.reply_blocked`。
7. `DeliveryService` 只消费已放行的回复并完成投递。
8. 首响之后再异步触发 `memory.signal.*`、`turn_reflection` 与更深层的治理逻辑。

### 数据与持久化

| 层级 | 文件 / 目录 | 用途 |
|------|-------------|------|
| 对话历史 | `sessions/<session_key>/dialogue.jsonl` | 用户可见的对话记录 |
| 内部历史 | `sessions/<session_key>/internal.jsonl` | `brain` 决策摘要、任务状态与规范化执行事实 |
| 执行断点 | `sessions/_checkpoints/worker.pkl` | `worker` 执行恢复状态 |
| 长期记忆 | `memory/memory.jsonl` | 长期记忆唯一事实源 |
| 向量镜像 | 本地向量索引 | `memory.jsonl` 的检索镜像，而非事实源 |
| 工作区技能 | `skills/<name>/SKILL.md` | 工作流沉淀与执行提示 |

### 工具、执行与反思

- `worker` 当前通过 `DeepAgentExecutor` 执行复杂任务，但这只是执行内核实现细节，外围协议已经切到 `brain + runtime + agent team`。
- 执行层可组合工具注册表、MCP 工具、工作区 `skills/` 与内置技能。
- `SubconsciousDaemon` 继续负责 PAD 衰减、周期性反思和主动消息机会检测。
- `MemoryGovernor` 与反思层只消费规范化输入，不再依赖旧 runtime 的旁路事件。

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
├── agent/
│   ├── context.py       # prompt 与记忆上下文构建
│   ├── reflection/      # 反思协调、记忆沉淀、技能物化
│   └── tool/            # 工具注册与执行层
├── background/          # 潜意识守护、周期反思、心跳任务
├── bootstrap.py         # RuntimeHost，系统装配宿主
├── brain/
│   ├── decision_packet.py
│   ├── dialogue_policy.py
│   ├── executive.py
│   ├── reply_builder.py
│   └── task_policy.py
├── bus/                 # 优先级总线、路由、订阅、interceptor
├── delivery/
│   └── service.py
├── execution/
│   ├── backend.py
│   ├── deep_agent_executor.py
│   ├── executor_context.py
│   ├── skills.py
│   ├── stream.py
│   ├── team.py
│   └── tool_runtime.py
├── memory/
│   └── governor.py
├── protocol/            # envelope / commands / events / task results
├── runtime/
│   ├── assignment.py
│   ├── input_gate.py
│   ├── kernel.py
│   ├── recovery.py
│   ├── running_task.py
│   ├── scheduler.py
│   ├── service.py
│   ├── state_machine.py
│   ├── task_store.py
│   ├── transport_bus.py
│   └── task_state.py
├── safety/
│   └── guard.py
├── session/
│   ├── history_store.py
│   └── thread_store.py
├── channels/
├── tools/
├── config/
├── skills/
├── templates/
└── tests/
```

---

## 设计结论

当前版本的核心一句话是：

`ExecutiveBrain 负责决策，RuntimeKernel 负责任务闭环，ThreadStore / MemoryGovernor 负责沉淀。`

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。
