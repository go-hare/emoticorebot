# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个以陪伴为主线的个人 AI 助手，当前采用明确的 **`main_brain / execution / memory / reflection`** 架构。

- `MainBrainFrontLoop` 是唯一对外主体，负责理解用户、决定下一步、形成最终表达
- `ExecutionRuntime` 负责工具调用、后台任务生命周期、结构化进展与结果
- `RuntimeKernel` 负责把输入、会话、主脑、执行、输出、投递与反思串成事件闭环
- `Memory` 与 `Reflection` 负责短期认知沉淀、长期记忆治理与经验结晶

详细文档：

- [docs/companion-main-brain-execution-architecture.zh-CN.md](docs/companion-main-brain-execution-architecture.zh-CN.md)
- [docs/companion-main-brain-execution-module-contracts.zh-CN.md](docs/companion-main-brain-execution-module-contracts.zh-CN.md)

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
      "mainBrainMode": {
        "model": "anthropic/claude-opus-4-5",
        "provider": "openrouter"
      },
      "executionMode": {
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

当前 gateway 接入的渠道都使用 WebSocket 或轮询模式，不需要本地监听端口。`gateway.port` 目前仅保留给未来的 webhook 型渠道。

---

## 架构

当前主通路如下：

```text
Inbound Message
  -> TransportBus
  -> ConversationGateway
  -> RuntimeKernel
  -> InputNormalizer
  -> SessionRuntime
  -> MainBrainFrontLoop
  -> ExecutionRuntime (on demand)
  -> OutputRuntime
  -> DeliveryRuntime
  -> ReflectionRuntime / ReflectionGovernor (async)
```

核心原则：

- 只有一个对外主体，不存在两个并列人格
- `MainBrainFrontLoop` 负责理解用户、决定是否触发执行、决定如何回复
- `ExecutionRuntime` 只负责执行，不直接面对用户发言
- `OutputRuntime / DeliveryRuntime` 负责把结果收束成 `inline / push / stream`
- `Reflection` 负责把当轮经验先沉淀到 `memory/cognitive_events.jsonl`，再由深反思治理到 `memory/memory.jsonl`

### 数据与持久化

| 层级 | 文件 / 目录 | 用途 |
|------|-------------|------|
| 对话历史 | `session/<session_id>/left.jsonl` | `用户 <-> 主脑前台` 原始记录 |
| 执行历史 | `session/<session_id>/right.jsonl` | `主脑 <-> 执行层` 原始内部记录 |
| 短期记忆 | `memory/cognitive_events.jsonl` | 近期认知事件、浅反思与深反思中间材料 |
| 长期记忆 | `memory/memory.jsonl` | 经过治理后的稳定事实、关系与经验 |
| 向量镜像 | `memory/vector/` | 长期记忆检索镜像，不是事实源 |
| 技能目录 | `skills/<name>/SKILL.md` | 工作流与技能提示 |

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
├── main_brain/
│   ├── context.py
│   ├── packet.py
│   ├── reply_policy.py
│   └── runtime.py
├── execution/
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
├── input/
│   └── normalizer.py
├── output/
│   └── runtime.py
├── memory/
│   ├── crystallizer.py
│   ├── retrieval.py
│   ├── store.py
│   └── vector_index.py
├── protocol/            # commands / events / contracts / models
├── runtime/
│   ├── kernel.py
│   └── transport_bus.py
├── session/
│   ├── history_store.py
│   ├── models.py
│   ├── runtime.py
│   └── thread_store.py
├── reflection/
│   ├── cognitive.py
│   ├── deep.py
│   ├── governor.py
│   ├── input.py
│   ├── manager.py
│   ├── persona.py
│   ├── runtime.py
│   └── turn.py
├── channels/
├── config/
├── cron/
├── models/
├── providers/
├── safety/
├── skills/
├── templates/
├── tools/
└── utils/
```

---

## 设计结论

当前版本的核心一句话是：

`MainBrainFrontLoop` 统一拥有任务语义与最终表达权，`ExecutionRuntime` 负责后台执行和结构化回写，`RuntimeKernel` 负责把整条链路闭环起来。

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。
