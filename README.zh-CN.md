# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个以陪伴为主线的个人 AI 助手，当前采用明确的 **`CompanionBrain -> SessionRuntime -> CentralExecutor`** 架构。

- `CompanionBrain` 负责理解用户、保持关系连续性、决定是否委托执行
- `SessionRuntime` 负责任务生命周期、输入门控、事件流和 session 级 live state
- `CentralExecutor` 负责复杂任务执行、工具调用、Deep Agents 集成
- `Reflection` 在首响之后异步运行，沉淀长期记忆与稳定模式

详细文档：

- [docs/non-compatible-runtime-refactor.zh-CN.md](docs/non-compatible-runtime-refactor.zh-CN.md)
- [docs/runtime-refactor-execution-checklist.zh-CN.md](docs/runtime-refactor-execution-checklist.zh-CN.md)

---

## 安装

从源码安装：

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

从 PyPI 安装：

```bash
pip install emoticorebot-ai
```

> 需要 Python >= 3.11。如需 Matrix E2EE 支持：`pip install "emoticorebot-ai[matrix]"`

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
      "centralMode": {
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

### 主通路

当前用户消息的主干链路是：

```text
Inbound Message
  -> ConversationGateway
  -> ThreadStore / HistoryStore
  -> CompanionBrain
  -> SessionRuntime (optional task submission)
  -> CentralExecutor
  -> Runtime Event Loop
  -> EventNarrator
  -> OutboundDispatcher
  -> ReflectionCoordinator (async)
```

系统明确遵守这几条边界：

- `brain` 是唯一对外主体，最终对用户说什么只能由它决定
- `runtime` 拥有任务表、运行句柄、输入门控和事件流
- `executor` 只执行任务，不直接改外发通道
- `thread store` 只做持久化，不持有 live runtime handle

### 核心组件

| 组件 | 职责 |
|------|------|
| `CompanionBrain` | 处理用户回合决策，决定直接回复、追问还是创建/续跑任务 |
| `RuntimeHost` | 顶层装配宿主，组装 gateway、brain、runtime、reflection 与后台服务 |
| `EventNarrator` | 把 runtime 的高层事件转换成适合陪伴场景的自然语言叙述 |
| `SessionRuntime` | 持有单个 session 的 live execution state、task table、input gate |
| `RuntimeManager` | 维护 `session_id -> SessionRuntime` 的映射与生命周期 |
| `CentralExecutor` | 调用 Deep Agents、工具注册表、技能与 checkpointer 执行复杂任务 |
| `ThreadStore` | 持久化对话历史、内部历史与 reflection 所需上下文 |
| `ReflectionCoordinator` | 基于规范化的 `ReflectionInput` 驱动 turn/deep reflection |

### 运行时协议

运行时输入输出已经统一为独立协议层：

- `emoticorebot/protocol/task_models.py`
- `emoticorebot/protocol/task_result.py`
- `emoticorebot/protocol/events.py`
- `emoticorebot/protocol/submissions.py`

这让主通路不再依赖裸 `dict` 事件，也让状态机测试可以直接围绕协议收敛。

### 异步行为

`RunningTask` 对应一次 live 执行实例，属于异步运行。

- 创建任务后，`SessionRuntime` 会启动后台执行
- 进度通过 runtime event 流异步上报
- 如果缺少信息，任务会进入 `awaiting_user` / `paused` 相关状态
- 用户补充信息后，runtime 再次异步恢复执行
- reflection 不阻塞首个用户回复

---

## 数据与持久化

| 层级 | 文件 / 目录 | 用途 |
|------|-------------|------|
| 对话历史 | `sessions/<session_key>/dialogue.jsonl` | 用户可见的对话记录 |
| 内部历史 | `sessions/<session_key>/internal.jsonl` | `brain` 决策、任务摘要、规范化执行事实 |
| 中央执行断点 | `sessions/_checkpoints/central.pkl` | Deep Agents 执行断点与恢复状态 |
| 长期记忆 | `memory/*.jsonl` | 稳定的自我、关系、洞察与认知事件 |
| 工作区技能 | `skills/<name>/SKILL.md` | 工作流沉淀与执行提示 |

---

## 工具与技能

`central` 可以组合以下能力：

- 工具注册表中的结构化工具
- MCP 暴露的外部工具
- 工作区 `skills/` 与内置技能
- Deep Agents 的内部规划与流式执行

当前技能加载入口位于：

- `emoticorebot/execution/skills.py`
- `emoticorebot/agent/reflection/skill.py`

---

## 后台与反思

后台行为由 `SubconsciousDaemon` 与反思协调器组成：

- `_decay_loop`：衰减 PAD 情绪状态
- `_reflect_loop`：周期性触发深层反思
- `_proactive_loop`：在合适时机主动发起消息

反思只消费规范化输入：

- thread messages
- internal history
- task snapshots
- normalized execution facts

这意味着反思层不再依赖早期的临时运行时副产物。

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
│   ├── companion_brain.py
│   ├── decision_packet.py
│   └── event_narrator.py
├── execution/
│   ├── backend.py
│   ├── central_executor.py
│   ├── executor_context.py
│   ├── skills.py
│   ├── stream.py
│   └── tool_runtime.py
├── protocol/            # typed submissions / events / task results
├── runtime/
│   ├── event_loop.py
│   ├── input_gate.py
│   ├── manager.py
│   ├── running_task.py
│   ├── session_runtime.py
│   └── task_state.py
├── session/
│   ├── history_store.py
│   └── thread_store.py
├── memory/
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

`CompanionBrain 负责决策，SessionRuntime 负责执行，ThreadStore 负责记忆。`

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。
