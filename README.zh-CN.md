# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个超轻量级个人 AI 助手，采用 **EQ 主导的融合架构（EQ + IQ Layer）**，基于 [LangGraph](https://github.com/langchain-ai/langgraph) 构建，源自原始 Nanobot 项目。

它能在每轮对话中感知情感上下文，让 **EQ 作为主导层**，并在需要时把复杂任务路由到一个基于 **Deep Agents** 的 IQ 执行层，同时通过后台反思机制持续演化人格。

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

### EQ 主导融合图（LangGraph）

emoticorebot 使用 **LangGraph 状态机**执行每轮对话。外层图仍然很简洁，但内部语义已经升级为 **EQ 主导讨论 + Deep Agents 执行**：

```
用户输入
    │
    ▼
会话历史 / pending-task 元数据
    │
    ▼
 ┌────────────────────────────────────────────────────────┐
 │                   LangGraph 图                         │
 │                                                        │
 │   ENTRY ──→ [EQ 节点] ──┬──→ [IQ Layer] ──┐           │
 │               ▲         │        │         │           │
 │               └─────────┘        ▼         │           │
 │                          [Memory 节点] ←───┘           │
 │                                │                       │
 └────────────────────────────────┼───────────────────────┘
                                  ▼
                              END / 输出
```

`EQ` 与 `IQ Layer` 在一轮内部可以多次往返，但**只有 EQ 有权决定结束本轮并生成最终对用户的话**。

**节点职责：**

| 节点 | 职责 |
|------|------|
| `EQ Node` | 主导层。负责理解意图、决定是否征询 IQ、跟踪任务承接，并最终对外表达。 |
| `IQ Layer` | 基于 Deep Agents 的执行层。负责规划、工具调用、子代理协作与复杂任务执行。 |
| `Memory Node` | 写入 event / episodic / semantic / relational / affective / plan 记忆，并保存 PAD 状态。 |

**路由逻辑（`FusionRouter`）：**

- `EQ → IQ`：EQ 判断当前回合需要理性分析或内部执行。
- `IQ → EQ`：IQ 返回任务分析、证据、风险、缺参以及建议动作。
- `EQ → Memory`：EQ 已决定直接回答用户或向用户追问缺参。
- `* → Memory`：`done=True` 时写回记忆并退出。

### IQ Layer 内部的 Deep Agents 执行模型

当前的 IQ 层不再使用旧的专家 / overlay 管线，而是改为 Deep Agents 执行模型，同时保持简洁的 EQ↔IQ 协议。

| 组件 | 职责 |
|---|---|---|
| Planner | 拆解内部任务并决定执行路径 |
| Tools | 执行 web search、fetch、message、cron 等注册能力 |
| Subagents | 承担研究、工作区操作等聚焦子任务 |
| Skills | 复用工作区 `skills/` 下的本地工作流说明 |

协议仍然由 **EQ 主导**：

- EQ 只决定是否需要 IQ，以及要下发什么内部任务
- IQ 使用工具、子代理和技能完成规划与执行
- IQ 返回标准化结果包：`status`、`analysis`、`evidence`、`risks`、`missing`、`recommended_action`
- EQ 决定是直接回答、向用户追问，还是继续内部讨论

这样外层状态机可以保持稳定，而内部 IQ 执行能力可以逐步增强。

### 典型工作流示例

#### 1. 普通请求 → 直接 IQ 执行

示例：“帮我总结一下这个文件。”

```text
用户输入
  → EQ 判断这是普通任务
  → EQ 向 IQ 下发明确内部任务
  → IQ 规划并执行
  → EQ 完成最终对外回复
```

特点：

- EQ 到 IQ 的直接交接
- 不把原始工具输出直接暴露给用户
- 最终语气仍由 EQ 收口

#### 2. 历史恢复 / 续聊 → EQ 承接 + IQ 继续执行

示例：

- 上一轮：“帮我查一下天气”
- assistant：“哪个城市？”
- 用户：“上海”

```text
用户补充输入
  → EQ 判断大概率是在恢复待续任务
  → EQ 从 session 和 memory 中恢复承接线索
  → IQ 带着恢复后的上下文继续执行任务
  → EQ 审核融合结果并自然地接上回复
```

特点：

- 适合未完成任务恢复
- 跨轮承接主要由 EQ 层负责
- 在上下文足够时，尽量避免重复追问同一个缺参

#### 3. 敏感 / 低置信度请求 → IQ 分析 + EQ 守门

示例：“执行这个命令，然后把旧文件删掉。”

```text
用户请求
  → EQ 判断可能涉及外部动作或更高风险
  → EQ 下发更谨慎的内部任务
  → IQ 评估可执行性、证据与保护条件
  → EQ 决定是谨慎回答、追问补充信息，还是继续内部讨论
```

特点：

- 适合安全敏感场景和过度自信抑制
- 在涉及工具或低置信度时尤其有价值
- 最终仍然保持统一的 EQ 说话风格

---

### 决策输入与提示词构建

当前实现里，已经不再使用独立的 `SignalExtractor` / `PolicyEngine` 模块。现在每轮的决策计划来自 **`EQService` + `FusionRouter` + session metadata**。

**EQ 提示词构建（`ContextBuilder.build_eq_system_prompt`）**

- 从工作区 `AGENTS.md` 载入 EQ 执行规则
- 从 `SOUL.md` 载入人格锚点，从 `USER.md` 载入用户认知
- 从 `current_state.md` 载入 PAD / 当前状态
- 检索 relational / affective / reflective / episodic 记忆片段
- 让 EQ 决定是否需要征询 IQ，以及该下发什么内部任务

**IQ 提示词构建（`ContextBuilder.build_iq_system_prompt`）**

- 从工作区 `AGENTS.md` 和 `TOOLS.md` 载入执行约束
- 载入 `current_state.md`
- 检索 semantic / plan / reflective 记忆片段
- 注入 active skills 摘要，以及被配置为常驻的技能内容
- 通过 `intent_params` 传入待续任务和缺参提示

**会话续接输入**

- 在 EQ 初判和后续 IQ 轮次前，注入 pending task 元数据
- assistant 侧 metadata 会持久化任务承接信息、任务短标签和压缩 IQ 摘要
- 轻量 IQ 状态如 `iq_status`、`iq_confidence`、`iq_missing_params` 会被保留用于续接
- `resume_task` 恢复能力帮助 EQ 在上下文足够时避免重复追问

也就是说，现在的真实工作流是：**历史 + 记忆 + 待续任务 → EQ 规划 → IQ 执行 → EQ 收束输出**。

---

### 记忆层

所有记忆以文件形式存储在 `~/.emoticorebot/data/memory/`（或配置的工作区）下：

| 记忆库 | 文件 | 用途 |
|--------|------|------|
| `EventStore` | `events.jsonl` | 每轮对话的原始事件流 |
| `EpisodicStore` | `episodic.jsonl` | 从事件切片提炼的情节记忆 |
| `SemanticStore` | `semantic.jsonl` | 带标签和重要性评分的持久事实 |
| `ReflectiveStore` | `reflective.jsonl` | 反思周期沉淀出的高阶洞察 |
| `PlanStore` | `plans.jsonl` | 进行中 / 阻塞 / 已完成的任务记忆 |
| `RelationalStore` | `relational.jsonl` | 偏好、关系和温情记忆 |
| `AffectiveStore` | `affective.jsonl` | PAD（愉悦/唤醒/支配）情绪时间轴 |
| `MemoryFacade` | — | 所有记忆库的统一读写入口 |

当前的主记忆链路已经切换为 **event stream → episodic / semantic / reflective / plans**，不再以 `MEMORY.md` / `HISTORY.md` 文件摘要作为主读取来源。

当前实现中，IQ 执行层默认只接收 **EQ 当前下发的内部任务**，不再回放用户/助手历史对话；历史承接与续聊判断由 EQ 层负责。

另外，assistant 侧的 session 历史现在只保留轻量记录：

- 用于后续回合恢复的压缩 `iq_summary`
- 用于轻量续接的 `iq_status`、`iq_confidence`、`iq_missing_params`
- `task_label` 与 pending-task 等任务承接元数据

热路径上的 `sessions/*.jsonl` 会刻意保持轻量，这样后续回合能恢复上下文，而不用回放所有内部 IQ 细节。

这样后续回合在恢复历史任务时，会更容易接上“上一次内部是怎么讨论的”。

现在，多轮 IQ 内部执行过程也会写入更长期的结构化记忆：

- assistant dialogue event 会保留最终 EQ 决策，方便追溯
- `ReflectiveStore` 会在出现多轮内部讨论或最终转向追问用户时写入 `iq_process` 反思记忆
- 后续检索拿到的不只是“发生了什么”，还包括“内部过程是怎么走到这里的”

**PAD 情感模型**（Pleasure-Arousal-Dominance）用于跨会话追踪机器人的连续情绪状态。每次启动时从 `current_state.md` 加载，每轮对话结束后写回。

---

### 当前局限

现在这套架构已经可用，但有些地方仍然是有意保持保守、轻量的：

- Deep Agents 的输出仍需要压缩成简洁的 EQ↔IQ 结果包，因此更丰富的中间执行轨迹暂未完整保留。
- 当前工具集合还是偏克制的，工作区操作和研究能力还可以继续扩展。
- 跨轮承接仍然以 EQ 为中心且偏保守，对隐式续聊的恢复还可以继续增强。
- reflective memory 目前保存的是压缩流程摘要，而不是完整执行轨迹。
- 外层 LangGraph 依然故意保持简单，主要复杂度仍然在 IQ 内核里。

### 路线图

我建议这套架构后续优先沿这几个方向演进：

1. **提升 Deep Agents 的可观测性**
   - 在不膨胀 session 历史的前提下保留更多执行轨迹
   - 为内部规划和子代理补更好的调试钩子

2. **增强任务承接恢复能力**
   - 提升对隐式续聊的识别能力
   - 更好地协调 pending-task / plan / memory 之间的信号

3. **继续加强流程记忆**
   - 给 `iq_process` 反思记忆补更多因果标签
   - 让后续回合不仅拿到结果，还能拿到触发该结果的失败模式
   - 为后面更强的 agent memory 行为做准备

4. **继续整理 IQ 层内部结构**
   - 把规划 / 执行 / 融合拆得更清楚
   - 在不牺牲轻量性的前提下提高可扩展性

5. **谨慎扩展工具、技能与子代理**
   - 前提是先把当前 Deep Agents 工作流打磨稳定
   - 之后可以再考虑更强的 research worker、workspace helper 和垂直领域技能

一句话总结：当前版本优先优化的是 **结构清晰、成本可控、历史可恢复**；下一阶段应该是在不放弃外层轻量化的前提下，继续提升 IQ 执行质量。

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
├── core/                 # 融合流程编排（LangGraph 图、状态、路由、上下文）
│   ├── graph.py          #   LangGraph 图定义与编译
│   ├── state.py          #   FusionState / IQState / EQState
│   ├── router.py         #   FusionRouter（EQ ↔ IQ ↔ Memory 路由）
│   ├── context.py        #   EQ / IQ 提示词上下文构建器
│   ├── model.py          #   LLMFactory（多 provider 支持）
│   ├── mcp.py            #   MCP 客户端集成
│   ├── skills.py         #   技能加载器
│   └── nodes/            #   eq_node / iq_node / memory_node
├── services/             # 服务层
│   ├── eq_service.py     #   EQ 主导服务（初判 / 终判）
│   ├── iq_service.py     #   IQ 执行层（Deep Agents + 子代理）
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
├── background/           # 后台守护进程
│   ├── subconscious.py   #   SubconsciousDaemon（衰减 / 反思 / 主动对话）
│   ├── reflection.py     #   ReflectionEngine（元认知反思）
│   ├── heartbeat.py      #   HeartbeatService（两阶段任务执行器）
│   └── subagent.py       #   后台子 agent 执行
├── tools/                # 内置工具实现
├── channels/             # 渠道适配器（Telegram、Discord 等）
├── providers/            # LLM provider 工具
├── runtime/              # FusionRuntime（调度 + 服务编排）
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
