# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个超轻量级个人 AI 助手，采用 **融合管线架构（IQ + EQ）**，基于 [LangGraph](https://github.com/langchain-ai/langgraph) 构建，源自原始 Nanobot 项目。

它能在每轮对话中感知情感上下文，动态平衡事实推理（IQ）与共情表达（EQ），并通过后台反思机制持续演化人格。

---

## 安装

从源码安装（推荐开发者）：

```bash
git clone https://github.com/HKUDS/emoticorebot.git
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
      "model": "anthropic/claude-opus-4-5",
      "provider": "openrouter"
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

### 融合管线（LangGraph）

emoticorebot 使用 **LangGraph 状态机**执行每轮对话。图中有三个节点和一个动态路由器：

```
用户输入
    │
    ▼
[SignalExtractor]  ──→  TurnSignals（task_strength, emotion_intensity,
    │                               relationship_need, urgency, safety_risk）
    ▼
[PolicyEngine]     ──→  FusionPolicy（iq_weight, eq_weight, empathy_depth,
    │                                 fact_depth, tool_budget, tone）
    ▼
 ┌────────────────────────────────────────────────────────┐
 │                   LangGraph 图                         │
 │                                                        │
 │   ENTRY ──→ [EQ 节点] ──┬──→ [IQ 节点] ──┐            │
 │               ▲         │        │        │            │
 │               └─────────┘        ▼        │            │
 │                          [Memory 节点] ←──┘            │
 │                                │                       │
 └────────────────────────────────┼───────────────────────┘
                                  ▼
                              END / 输出
```

**节点职责：**

| 节点 | 职责 |
|------|------|
| `EQ Node` | 情感感知、共情解析、回复风格渲染。判断是否需要委托 IQ 执行任务。 |
| `IQ Node` | 事实推理、工具调用（网络搜索、文件操作、代码执行、MCP）。 |
| `Memory Node` | 将对话写入语义/关系/情感记忆库，保存 PAD 情绪状态。 |

**路由逻辑（`FusionRouter`）：**

- `EQ → IQ`：EQ 识别到需要事实执行的任务。
- `IQ → EQ`：IQ 完成执行，结果交回 EQ 进行共情包装。
- `* → Memory`：`done=True` 或 IQ 尝试次数达到上限时，写回记忆并退出。

---

### 信号与策略层

`SignalExtractor` 将每轮用户输入解析为五个 `[0, 1]` 浮点信号：

| 信号 | 含义 |
|------|------|
| `task_strength` | 动作关键词检测（"查询"、"run"、"fix" 等） |
| `emotion_intensity` | 情绪关键词 + 感叹号密度 |
| `relationship_need` | 从情绪强度 + "你"代词出现频率推导 |
| `urgency` | 紧急词（"立刻"、"asap" 等）+ 问号 |
| `safety_risk` | 危机词硬编码检测（自伤相关 → 1.0） |

`PolicyEngine` 将信号转换为 `FusionPolicy`：

| 策略字段 | 作用 |
|---|---|
| `iq_weight / eq_weight` | 事实处理与共情处理的权重比 |
| `empathy_depth` | 0 = 无共情，1 = 轻度，2 = 深度共情开场 |
| `fact_depth` | IQ 推理深度（1–3） |
| `tool_budget` | 每轮最大工具调用次数（3–6） |
| `tone` | 输出风格：`professional` / `warm` / `balanced` / `concise` |

`ReflectionEngine` 产生的运行时调整可通过 `eq_bias`、`iq_bias`、`tone_preference` 偏置策略参数。

---

### 记忆层

所有记忆以文件形式存储在 `~/.emoticorebot/data/`（或配置的工作区）下：

| 记忆库 | 文件 | 用途 |
|--------|------|------|
| `SemanticStore` | `semantic_memories.jsonl` | 带标签和重要性评分的事实笔记 |
| `RelationalStore` | `relational_memories.jsonl` | 偏好、关系和温情记忆 |
| `AffectiveStore` | `affective_traces.jsonl` | PAD（愉悦/唤醒/支配）情绪时间轴 |
| `PolicyStateStore` | `policy_state.json` | 带 TTL 的活跃运行时策略调整 |
| `MemoryFacade` | — | 所有记忆库的统一读写入口 |

**PAD 情感模型**（Pleasure-Arousal-Dominance）用于跨会话追踪机器人的连续情绪状态。每次启动时从 `current_state.md` 加载，每轮对话结束后写回。

---

### 后台进程

三个异步守护进程在后台独立运行：

#### SubconsciousDaemon（潜意识守护进程）
三条并发 `asyncio.Task` 循环：

| 循环 | 默认间隔 | 行为 |
|------|---------|------|
| `_decay_loop` | 30 分钟 | 逐步衰减 PAD 驱动值趋向中性 |
| `_reflect_loop` | 1 小时 | 触发 `ReflectionEngine` 更新 SOUL/USER 文件 |
| `_proactive_loop` | 10 分钟 | 在空闲时随机向用户主动发起一条消息 |

#### ReflectionEngine（元认知反思引擎）
读取最近的关系记忆，调用 LLM 生成结构化 JSON，包含：

- **`soul_update`** — 微调 `SOUL.md`（人格演化，保留原有锚点）
- **`user_update`** — 向 `USER.md` 追加新的用户洞察
- **`policy_adjustment`** — 设置 `eq_bias`、`iq_bias`、`tone_preference`、`tool_budget_delta`、`duration_hours`

`SOUL.md` 和 `USER.md` 的更新在写入前都经过**验证器**校验，写入操作是原子的（临时文件 → 重命名，并保留备份）。

#### HeartbeatService（心跳任务服务）
两阶段后台任务检查器：

1. **阶段一（决策）**：LLM 读取 `HEARTBEAT.md`，通过工具调用返回 `heartbeat({action: "skip"|"run"})`。
2. **阶段二（执行）**：仅当返回 `run` 时，触发注册的 `on_execute` 回调。

---

### 内置工具

IQ 节点可调用的内置工具：

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

IQ 和 EQ 可以分别配置**不同模型**（`iq.model` / `eq.model`），从而灵活权衡成本与质量。

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
├── core/                 # 融合流程编排（LangGraph 图、节点、路由、策略）
│   ├── graph.py          #   LangGraph 图定义与编译
│   ├── state.py          #   FusionState / IQState / EQState
│   ├── signal_extractor.py  # TurnSignals 提取
│   ├── policy_engine.py  #   FusionPolicy 生成
│   ├── router.py         #   FusionRouter（节点路由逻辑）
│   ├── model.py          #   LLMFactory（多 provider 支持）
│   ├── mcp.py            #   MCP 客户端集成
│   ├── skills.py         #   技能加载器
│   ├── context.py        #   提示词上下文构建器
│   └── nodes/            #   eq_node / iq_node / memory_node
├── services/             # 服务层
│   ├── eq_service.py     #   EQ 服务（共情渲染）
│   ├── iq_service.py     #   IQ 服务（工具增强推理）
│   ├── memory_service.py #   记忆读写服务
│   └── tool_manager.py   #   工具注册与执行
├── memory/               # 分层记忆实现
│   ├── semantic_store.py
│   ├── relational_store.py
│   ├── affective_store.py
│   ├── policy_state_store.py
│   └── memory_facade.py
├── background/           # 后台守护进程
│   ├── subconscious.py   #   SubconsciousDaemon（衰减 / 反思 / 主动对话）
│   ├── reflection.py     #   ReflectionEngine（元认知反思）
│   ├── heartbeat.py      #   HeartbeatService（两阶段任务执行器）
│   └── subagent.py       #   后台子 agent 执行
├── tools/                # 内置工具实现
├── channels/             # 渠道适配器（Telegram、Discord 等）
├── providers/            # LLM provider 工具
├── runtime/              # FusionRuntime（调度 + 服务编排）
├── bus/                  # MessageBus（入站/出站事件队列）
├── cron/                 # 定时任务调度服务
├── session/              # 会话管理
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
