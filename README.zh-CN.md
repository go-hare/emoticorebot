# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个超轻量级个人 AI 助手，采用 **`main_brain -> executor`** 架构，源自原始 Nanobot 项目。

它以 `main_brain` 作为唯一对外主体，在需要时把复杂任务委托给基于 **Deep Agents** 的 `executor`，并通过 `light_insight + deep_insight` 持续演化。

详细设计文档：

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md)
- [docs/FIELDS.zh-CN.md](docs/FIELDS.zh-CN.md)

---

## 安装

从源码安装（推荐开发者）：

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

从 PyPI 安装：

```bash
pip install emoticorebot-ai
```

> 需要 Python ≥ 3.11。如需 Matrix E2EE 支持：`pip install "emoticorebot-ai[matrix]"`

---

## 快速开始

**1. 初始化本地工作区：**

```bash
emoticorebot onboard
```

此命令将创建 `~/.emoticorebot/` 目录，包含默认配置文件、`SOUL.md`（人格文件）、`USER.md`（用户档案）和 `HEARTBEAT.md`（后台任务队列）。

**2. 编辑 `~/.emoticorebot/config.json`（最小配置）：**

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
      "executorMode": {
        "model": "anthropic/claude-opus-4-5",
        "provider": "openrouter"
      }
    }
  }
}
```

**3. CLI 对话：**

```bash
emoticorebot agent
```

**4. 启动网关（对接聊天渠道）：**

```bash
emoticorebot gateway
```

---

## 架构

### `main_brain -> executor` 循环

emoticorebot 现在使用**显式调度循环**而不是外层 LangGraph 状态机。运行时保持简单：`main_brain` 负责判断，必要时调用 `executor`，对用户首响应之后再异步做反思。

```
用户输入
    │
    ▼
session / internal / checkpointer
    │
    ▼
main_brain ──→ executor（按需）
    │              │
    └──────←───────┘
    │
    ▼
用户回复
    │
    ▼
cognitive_event -> light_insight -> deep_insight -> memory
```

只有 `main_brain` 有权结束当前轮并生成最终对用户的话。

**运行时职责：**

| 组件 | 职责 |
|------|------|
| `main_brain` | 唯一主体。负责理解意图、控制 executor、维持关系连续性并最终对外表达。 |
| `executor` | 基于 Deep Agents 的执行层。负责规划、工具调用、技能复用与复杂任务执行。 |
| `reflection` | 异步后处理层。每轮产出 `light_insight`，按需或按周期信号追加 `deep_insight`。 |

**单轮协议：**

- `main_brain -> executor`：只下发一个清晰的内部执行问题；若是恢复执行，再附带恢复元数据。
- `executor -> main_brain`：返回紧凑结果包：`control_state`、`status`、`analysis`、`risks`、`missing`、`recommended_action`、`confidence`，以及可选的 `pending_review`。
- `main_brain -> user`：只有 `main_brain` 能决定直接回答、追问用户，还是继续内部执行。
- `post-turn reflection`：对用户首响应之后，运行时再写入轮次记录、提炼 `cognitive_event`、执行 `light_insight`，并按需调度 `deep_insight`。

### `executor` 内部的 Deep Agents 执行模型

当前的 `executor` 不再使用旧的专家 / overlay 管线，而是改为 Deep Agents 执行模型，同时保持简洁的 `main_brain -> executor` 协议。

| 组件 | 职责 |
|---|---|
| Planner | 拆解内部任务并决定执行路径 |
| Tools | 执行文件操作、Shell、web search、fetch、message、cron 等注册能力 |
| Step-level concurrency | 在安全前提下并行处理彼此独立的步骤或工具链 |
| Skills | 复用工作区 `skills/` 下的本地工作流说明 |

协议仍然由 **main_brain 主导**：

- `main_brain` 只决定是否需要 `executor`，以及要下发什么内部任务
- `executor` 使用工具、技能以及步骤级并发完成规划与执行
- `executor` 返回标准化结果包：`control_state`、`status`、`analysis`、`risks`、`missing`、`recommended_action`、`confidence`
- `main_brain` 决定是直接回答、向用户追问，还是继续内部讨论

这样外层循环保持稳定，而内部执行内核可以逐步增强。

### 典型工作流示例

#### 1. 普通请求 → 委托执行

示例：“帮我总结一下这个文件。”

```text
用户输入
  → main_brain 判断需要 executor 帮忙
  → main_brain 下发明确内部任务
  → executor 规划并执行
  → main_brain 完成最终对外回复
```

特点：

- `main_brain` 到 `executor` 的直接交接
- 不把原始工具输出直接暴露给用户
- 最终语气仍由 `main_brain` 收口

#### 2. 历史恢复 / 续聊 → 承接上下文并恢复执行

示例：

- 上一轮：“帮我查一下天气”
- assistant：“哪个城市？”
- 用户：“上海”

```text
用户补充输入
  → main_brain 判断大概率是在恢复待续任务
  → main_brain 从 session、internal 和暂停执行状态中恢复承接线索
  → executor 带着恢复后的上下文继续执行任务
  → main_brain 审核融合结果并自然地接上回复
```

特点：

- 适合未完成任务恢复
- 跨轮承接主要由 `main_brain` 负责
- 在上下文足够时，尽量避免重复追问同一个缺参

#### 3. 敏感 / 低置信度请求 → executor 分析 + main_brain 守门

示例：“执行这个命令，然后把旧文件删掉。”

```text
用户请求
  → main_brain 判断可能涉及外部动作或更高风险
  → main_brain 下发更谨慎的内部任务
  → executor 评估可执行性、风险与保护条件
  → main_brain 决定是谨慎回答、追问补充信息，还是继续内部讨论
```

特点：

- 适合安全敏感场景和过度自信抑制
- 在涉及工具或低置信度时尤其有价值
- 最终仍然保持统一的 `main_brain` 说话风格

---

### 决策输入与提示词构建

当前实现已经不再依赖外层 router。每轮计划直接来自 `main_brain`、会话状态和 `executor` 结果包。

**`main_brain` 提示词构建（`ContextBuilder.build_main_brain_system_prompt`）**

- 从工作区 `AGENTS.md` 载入 `main_brain` 规则
- 从 `SOUL.md` 载入人格锚点，从 `USER.md` 载入用户认知
- 从 `current_state.md` 载入 PAD / 当前状态
- 检索最近的 `cognitive_event`
- 让 `main_brain` 决定是直接回答，还是委托给 `executor`

**`executor` 提示词构建（`ExecutorService._build_agent_instructions`）**

- 固化 `main_brain -> executor` 协议
- 注入工作区 / 内置技能路径与技能摘要
- 限制最终输出为紧凑执行结果包
- 结合工具注册表、Deep Agents 后端路由和 checkpointer 恢复状态执行

**会话续接输入**

- `dialogue.jsonl` 保存用户可见对话历史
- `internal.jsonl` 保存紧凑的 `main_brain <-> executor` 摘要和控制决策
- 暂停执行元数据保存 `thread_id`、`run_id`、`missing` 和 `pending_review`
- checkpointer 状态让 `executor` 从上次中断点继续

也就是说，现在的真实工作流是：**历史 + 认知上下文 + 暂停执行状态 → `main_brain` 规划 → `executor` 执行（按需）→ `main_brain` 收束输出**。

---

### 记忆层

当前架构把**运行时材料**、**认知事件**和**长期记忆**分开保存：

| 层 | 文件 / 存储 | 用途 |
|--------|------|------|
| `session` | `sessions/<session_key>/dialogue.jsonl` | 用户可见的 `user <-> main_brain` 对话 |
| `internal` | `sessions/<session_key>/internal.jsonl` | 紧凑的 `main_brain <-> executor` 摘要、控制动作、暂停/恢复线索 |
| `checkpointer` | `sessions/_checkpoints/executor.pkl` | `executor` 暂停 / 恢复状态 |
| `cognitive_event` | `memory/cognitive_events.jsonl` | 每轮结束后提炼出的结构化认知切片 |
| `self_memory` | `memory/self_memory.jsonl` | 稳定的主脑自我模式 |
| `relation_memory` | `memory/relation_memory.jsonl` | 稳定的用户 / 关系认知 |
| `insight_memory` | `memory/insight_memory.jsonl` | 深反思、稳定执行模式、技能候选 |

当前的运行链路是：

1. 先写入 `dialogue` 和 `internal`
2. 再从完整轮次材料提炼 `cognitive_event`
3. 每轮必做 `light_insight`
4. 只有当 `main_brain` 判断值得深挖，或周期信号触发时，才调度 `deep_insight`
5. 只有稳定结论才进入长期记忆，并可进一步更新 `SOUL.md`、`USER.md` 或未来 `skills`

`executor` 默认只接收当前被委托的内部问题和恢复元数据，不回放完整用户历史；跨轮承接仍由 `main_brain` 主导。

**PAD 情感模型**（Pleasure-Arousal-Dominance）用于跨会话追踪机器人的连续情绪状态。每次启动时从 `current_state.md` 加载，每轮对话结束后写回。

---

### 当前局限

现在这套架构已经可用，但有些地方仍然是有意保持保守、轻量的：

- Deep Agents 的输出仍需要压缩成紧凑的 `executor` 结果包，因此更丰富的中间执行轨迹主要还保留在运行时材料里。
- 当前工具集合还是偏克制的，工作区操作和研究能力还可以继续扩展。
- 跨轮承接仍然以 `main_brain` 为中心且偏保守，对隐式续聊的恢复还可以继续增强。
- `deep_insight` 当前保存的是稳定摘要，而不是完整原始执行轨迹。
- 能力升级到 `skills` 的过程仍然偏保守，主要依赖反思而不是自动提升。

### 路线图

我建议这套架构后续优先沿这几个方向演进：

1. **提升 Deep Agents 的可观测性**
   - 在不膨胀 session 历史的前提下保留更多执行轨迹
   - 为内部规划和执行补更好的调试钩子

2. **增强任务承接恢复能力**
   - 提升对隐式续聊的识别能力
   - 更好地协调暂停执行状态、记忆和用户补充信息之间的信号

3. **继续加强反思输出**
   - 给 `light_insight.execution_review` 和 `deep_insight` 补更多因果标签
   - 让后续回合不仅拿到结果，还能拿到触发该结果的失败模式

4. **继续整理 executor 内部结构**
   - 把规划 / 执行 / 融合拆得更清楚
   - 在不牺牲轻量性的前提下提高可扩展性

5. **谨慎扩展工具与技能**
   - 前提是先把当前 Deep Agents 工作流打磨稳定
   - 之后可以再考虑更强的 workspace helper、验证流程和垂直领域技能

一句话总结：当前版本优先优化的是 **结构清晰、成本可控、历史可恢复**；下一阶段应该是在不增加额外外层编排的前提下，继续提升 `executor` 的执行质量。

---

### 后台进程

当前后台行为由一个守护进程加若干共享服务组成：

#### SubconsciousDaemon（潜意识守护进程）
三条并发 `asyncio.Task` 循环：

| 循环 | 默认间隔 | 行为 |
|------|---------|------|
| `_decay_loop` | 30 分钟 | 逐步衰减 PAD 驱动值趋向中性 |
| `_reflect_loop` | 1 小时 | 通过 `ReflectionEngine` 触发周期性 `deep_insight` |
| `_proactive_loop` | 10 分钟 | 在空闲时随机向用户主动发起一条消息 |

#### ReflectionEngine（元认知反思引擎）
由潜意识反思循环调用。它会以周期信号运行 `deep_insight`，并可能：

- 向 `self_memory.jsonl`、`relation_memory.jsonl`、`insight_memory.jsonl` 追加稳定记忆
- 在确认稳定自我模式后重写 `SOUL.md`
- 在确认稳定用户模式后重写 `USER.md`

`SOUL.md` 和 `USER.md` 的更新在写入前都会经过校验，写入操作保持原子性。

#### HeartbeatService（心跳任务服务）
两阶段后台任务检查器：

1. **阶段一（决策）**：LLM 读取 `HEARTBEAT.md`，通过工具调用返回 `heartbeat({action: "skip"|"run"})`。
2. **阶段二（执行）**：仅当返回 `run` 时，触发注册的 `on_execute` 回调。

---

### 内置工具

`executor` 可调用的内置工具：

| 工具 | 描述 |
|------|------|
| `web_search` | Brave Search API 网络搜索 |
| `web_fetch` | 抓取并解析网页内容（可读性处理） |
| `exec` | 执行 Shell 命令或代码片段 |
| `read_file` / `write_file` | 文件系统读写 |
| `list_dir` | 目录列表 |
| `system_info` | 操作系统与环境信息 |
| MCP 工具 | 通过配置的 MCP 服务器暴露的任何工具 |

---

### 技能（Skills）

技能是基于 Markdown 的提示插件，运行时从 `~/.emoticorebot/skills/` 或 `emoticorebot/skills/` 加载：

| 技能 | 用途 |
|------|------|
| `cron` | 定时或一次性任务调度 |
| `memory` | 显式记忆管理命令 |
| `github` | GitHub API 交互 |
| `clawhub` | ClawHub 集成 |
| `summarize` | 文档 / URL 摘要 |
| `tmux` | tmux 会话自动化 |
| `weather` | 天气查询 |
| `skill-creator` | 快速创建新技能脚手架 |

---

## 渠道支持

当前支持的聊天渠道（在 `config.json` 的 `channels` 字段配置）：

| 渠道 | 说明 |
|------|------|
| Telegram | 通过 `@BotFather` 申请 Bot Token；支持代理 |
| Discord | Gateway WebSocket；Intents 可配置 |
| WhatsApp | 需要 `bridge/` Node.js 桥接服务 |
| 飞书（Feishu） | WebSocket 长连接 |
| 钉钉（DingTalk） | Stream 流式模式 |
| Slack | Slack SDK；自动 Markdown 转换 |
| Email | IMAP（收信）+ SMTP（发信） |
| QQ | qq-botpy |
| Matrix（Element） | nio 库；支持 E2EE 加密 |
| Mochat | Socket.IO |

所有渠道将消息封装为 `InboundMessage` 事件发送到 `MessageBus`，并从 Runtime 接收 `OutboundMessage` 输出。

---

## MCP（Model Context Protocol）

通过配置接入任意 MCP 服务：

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxx"
        }
      }
    }
  }
}
```

---

## LLM 模型支持

emoticorebot 通过 **LangChain** 适配器和 **litellm** 支持多种模型：

| 提供商 | 依赖包 |
|--------|--------|
| OpenAI / OpenRouter | `langchain-openai` |
| Anthropic（Claude） | `langchain-anthropic` |
| Google Gemini | `langchain-google-genai` |
| Groq | `langchain-groq` |
| Ollama（本地模型） | `langchain-ollama` |

`main_brain` 和 `executor` 可以分别配置**不同模型**，对应 `agents.defaults.mainBrainMode` 与 `agents.defaults.executorMode`（也兼容 snake_case 键），从而灵活权衡成本与质量。

---

## 定时任务（Cron）

支持三种调度模式：

```json
{ "kind": "cron",  "expr": "0 9 * * *", "tz": "Asia/Shanghai" }
{ "kind": "every", "every_ms": 3600000 }
{ "kind": "at",    "at_ms": 1700000000000 }
```

CLI 管理：

```bash
emoticorebot cron list
```

---

## 安全建议

生产环境建议开启工作区沙箱，限制工具只能访问工作区内的文件：

```json
{
  "tools": {
    "restrictToWorkspace": true
  }
}
```

---

## Docker

```bash
docker build -t emoticorebot .
docker run -v ~/.emoticorebot:/root/.emoticorebot --rm emoticorebot onboard
docker run -v ~/.emoticorebot:/root/.emoticorebot -p 18790:18790 emoticorebot gateway
```

或使用 Docker Compose：

```bash
docker-compose up
```

---

## 常用命令

```bash
emoticorebot onboard          # 初始化工作区
emoticorebot agent            # 启动交互式 CLI 对话
emoticorebot gateway          # 启动网关（所有已启用的渠道）
emoticorebot status           # 查看运行时状态
emoticorebot cron list        # 列出定时任务
emoticorebot channels status  # 查看渠道连接状态
```

---

## 项目结构

```text
emoticorebot/
├── core/                 # 单轮流程编排（显式循环、状态、上下文）
│   ├── turn_loop.py      #   显式 main_brain -> executor 调度循环
│   ├── state.py          #   TurnState / MainBrainState / ExecutorState
│   ├── context.py        #   共享提示词上下文构建器
│   ├── model.py          #   LLMFactory（多 provider 支持）
│   ├── mcp.py            #   MCP 客户端集成
│   ├── skills.py         #   技能加载器
│   └── nodes/            #   main_brain_node / executor_node
├── services/             # 服务层
│   ├── main_brain_service.py # 主脑服务（初判 / 终判 / 控制）
│   ├── executor_service.py   # 执行系统（Deep Agents 执行内核）
│   ├── memory_service.py #   记忆读写服务
│   └── tool_manager.py   #   工具注册与执行
├── memory/               # 分层记忆实现
│   ├── structured_stores.py
│   ├── stateful_stores.py
│   ├── extractor.py
│   ├── retriever.py
│   ├── schema.py
│   ├── jsonl_store.py
│   └── memory_facade.py
├── background/           # 后台守护进程 + 周期反思入口
│   ├── subconscious.py   #   SubconsciousDaemon（衰减 / 反思 / 主动对话）
│   ├── reflection.py     #   ReflectionEngine（周期性 deep_insight 桥接）
│   ├── heartbeat.py      #   HeartbeatService（两阶段任务执行器）
├── tools/                # 内置工具实现
├── channels/             # 渠道适配器（Telegram、Discord 等）
├── providers/            # LLM provider 工具
├── runtime/              # EmoticoreRuntime（调度 + turn loop + 反思调度）
├── bus/                  # 入站 / 出站事件队列
├── cron/                 # 定时任务调度服务
├── session/              # 会话持久化与恢复
├── models/               # 共享数据模型（EmotionState 等）
├── config/               # Pydantic 配置 Schema
├── skills/               # 内置技能定义（Markdown）
├── templates/            # 初始化文件模板
├── utils/                # 公共工具函数
└── cli/                  # CLI 入口（Typer）
```

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。
