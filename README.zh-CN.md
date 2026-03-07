# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个超轻量级个人 AI 助手，采用 **EQ 主导的融合架构（EQ + IQ Layer）**，基于 [LangGraph](https://github.com/langchain-ai/langgraph) 构建，源自原始 Nanobot 项目。

它能在每轮对话中感知情感上下文，让 **EQ 作为主导层**，并在需要时把任务路由到一个轻量级、稀疏激活的 **IQ 专家层（Sparse MoE）**，同时通过后台反思机制持续演化人格。

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

emoticorebot 使用 **LangGraph 状态机**执行每轮对话。外层图仍然很简洁，但内部语义已经升级为 **EQ 主导讨论 + 轻量稀疏 MoE**：

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
| `EQ Node` | 主导层。负责理解意图、设定情绪目标、决定是否征询 IQ、选择专家、审核专家分歧，并最终对外表达。 |
| `IQ Layer` | 轻量稀疏 MoE。默认运行 `ActionExpert`，按需追加 `MemoryOverlay` 与 `RiskOverlay`。 |
| `Memory Node` | 写入 event / episodic / semantic / relational / affective / plan 记忆，并保存 PAD 状态。 |

**路由逻辑（`FusionRouter`）：**

- `EQ → IQ`：EQ 判断当前回合需要理性分析或内部专家协助。
- `IQ → EQ`：IQ 返回融合结果、专家包以及分歧摘要。
- `EQ → Memory`：EQ 已决定直接回答用户或向用户追问缺参。
- `* → Memory`：`done=True` 时写回记忆并退出。

### IQ Layer 内部的轻量稀疏 MoE

当前的 IQ 层不是重型全专家并行，而是刻意做成轻量版本。

| 专家 | 默认启用 | 职责 |
|---|---|---|
| `ActionExpert` | 是 | 主专家，负责事实分析、工具调用、缺参判断和下一步建议 |
| `MemoryOverlay` | 条件启用 | 在命中续聊 / 待续任务 / 历史计划时补充历史上下文 |
| `RiskOverlay` | 条件启用 | 在低置信度、调用工具或敏感动作时补充风险判断 |

专家选择由 **EQ 主导**：

- 默认只选 `ActionExpert`
- 只有历史承接 / 待续任务恢复时才加 `MemoryOverlay`
- 只有不确定、高风险、敏感动作时才加 `RiskOverlay`
- 为了控制 token 和延迟，最多只激活 **2 个专家**

每一轮 IQ 不只返回一个融合包，还会返回底层 `expert_packets`，这样 EQ 能看到内部专家的分歧，而不是只看到一个被抹平的结论。

### 典型工作流示例

#### 1. 普通请求 → `ActionExpert`

示例：“帮我总结一下这个文件。”

```text
用户输入
  → EQ 判断这是普通任务
  → EQ 选择: [ActionExpert]
  → IQ Layer 运行 ActionExpert
  → EQ 完成最终对外回复
```

特点：

- 成本最低的默认路径
- 不额外查历史补丁
- 只有在置信度下降时才可能追加风险补丁

#### 2. 历史恢复 / 续聊 → `ActionExpert + MemoryOverlay`

示例：

- 上一轮：“帮我查一下天气”
- assistant：“哪个城市？”
- 用户：“上海”

```text
用户补充输入
  → EQ 判断大概率是在恢复待续任务
  → EQ 选择: [ActionExpert, MemoryOverlay]
  → MemoryOverlay 检查 pending task / plans / episodic memory
  → ActionExpert 带着 overlay 上下文继续任务
  → EQ 审核融合结果并自然地接上回复
```

特点：

- 适合未完成任务恢复
- 会把 `resume_task` 和命中类型写入 session metadata
- 在上下文足够时，尽量避免重复追问同一个缺参

#### 3. 敏感 / 低置信度请求 → `ActionExpert + RiskOverlay`

示例：“执行这个命令，然后把旧文件删掉。”

```text
用户请求
  → EQ 判断可能涉及外部动作或更高风险
  → EQ 选择: [ActionExpert, RiskOverlay]
  → ActionExpert 评估可执行性和工具路径
  → RiskOverlay 指出风险 / 不确定性 / 缺少的保护条件
  → EQ 看到内部差异后，保守回答或先向用户确认
```

特点：

- 适合安全敏感场景和过度自信抑制
- 在涉及工具或低置信度时尤其有价值
- 只加一个 overlay，仍保持整体轻量

---

### 决策输入与提示词构建

当前实现里，已经不再使用独立的 `SignalExtractor` / `PolicyEngine` 模块。现在每轮的决策计划来自 **`EQService` + `FusionRouter` + session metadata**。

**EQ 提示词构建（`ContextBuilder.build_eq_system_prompt`）**

- 从工作区 `AGENTS.md` 载入 EQ 执行规则
- 从 `SOUL.md` 载入人格锚点，从 `USER.md` 载入用户认知
- 从 `current_state.md` 载入 PAD / 当前状态
- 检索 relational / affective / reflective / episodic 记忆片段
- 让 EQ 决定是否需要征询 IQ、该激活哪些专家、每个专家该关注什么

**IQ 提示词构建（`ContextBuilder.build_iq_system_prompt`）**

- 从工作区 `AGENTS.md` 和 `TOOLS.md` 载入执行约束
- 载入 `current_state.md`
- 检索 semantic / episodic / plan / reflective / event 记忆片段
- 注入 active skills 摘要，以及被配置为常驻的技能内容
- 通过 `intent_params` 传入 EQ 选定的专家和专家级问题

**会话续接输入**

- 在 EQ 初判和后续 IQ 轮次前，注入 pending task 元数据
- assistant 侧 metadata 会持久化 selected experts、expert packets、分歧摘要和 memory overlay 锚点
- EQ 仲裁结果现在也会持久化：包括采纳了哪些专家、压过了哪些专家，以及一条简短裁决摘要
- `MemoryOverlay` 能恢复 `resume_task` 和命中类型，帮助 EQ 在上下文足够时避免重复追问

也就是说，现在的真实工作流是：**历史 + 记忆 + 待续任务 → EQ 规划 → IQ 稀疏专家执行 → EQ 收束输出**。

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

另外，assistant 侧的 session metadata 现在还会保留：

- 本轮启用的专家列表
- 专家分歧摘要
- 专家逐项摘要
- EQ 仲裁结果（`accepted_experts` / `rejected_experts` / `arbitration_summary`）
- `MemoryOverlay` 的命中类型 / `resume_task` / overlay 摘要

这样后续回合在恢复历史任务时，会更容易接上“上一次内部是怎么讨论的”。

现在 EQ 的仲裁结果还会写入更长期的结构化记忆：

- assistant dialogue event 会保留仲裁 metadata，方便追溯
- `ReflectiveStore` 会在出现真实专家选择 / 否决 / 多轮内部讨论时写入 `eq_arbitration` 反思记忆
- 后续检索拿到的不只是“发生了什么”，还包括“EQ 当时为什么这样裁决专家”

**PAD 情感模型**（Pleasure-Arousal-Dominance）用于跨会话追踪机器人的连续情绪状态。每次启动时从 `current_state.md` 加载，每轮对话结束后写回。

---

### 当前局限

现在这套架构已经可用，但有些地方仍然是有意保持保守、轻量的：

- `MemoryOverlay` 目前还是 **规则优先**，还不是完全语义化的恢复层；这样更省 token、更快，但对更隐式的续聊识别还不够强。
- `RiskOverlay` 目前还是 **廉价启发式 overlay**，还不是小模型风险专家；适合轻量守护，但还不是深度挑错器。
- IQ 层融合目前仍然是 **以 `ActionExpert` 为主包**，overlay 专家更多是在补充、约束和修正，而不是完整对等仲裁。
- EQ 仲裁现在已经会写入历史和 reflective memory，但目前存下来的仍是压缩后的裁决洞察，而不是完整多轮辩论轨迹。
- 外层 LangGraph 目前仍然故意保持简单；系统行为已经接近 EQ 主导的稀疏 MoE，但图本身还不是一个专门的多专家状态机。

### 路线图

我建议这套架构后续优先沿这几个方向演进：

1. **把 `RiskOverlay` 升级成小模型专家**
   - 让风险审查更准确
   - 继续保持只在必要时启用，不拖慢主路径

2. **增强 `MemoryOverlay` 的恢复能力**
   - 提升对隐式续聊的识别能力
   - 更好地协调 pending-task / plan / episodic 之间的信号

3. **继续加强 EQ 仲裁记忆**
   - 给 `eq_arbitration` 反思记忆补更多因果标签
   - 让后续回合不仅拿到裁决结果，还能拿到触发该裁决的失败模式
   - 为后面更强的 agent memory 行为做准备

4. **继续整理 IQ 层内部结构**
   - 把规划 / 执行 / 融合拆得更清楚
   - 在不牺牲轻量性的前提下提高可扩展性

5. **后续再考虑增加更多专家**
   - 前提是先把 `ActionExpert`、`MemoryOverlay`、`RiskOverlay` 打磨稳定
   - 之后可以再考虑事实专家、社交记忆专家、深度计划专家等

一句话总结：当前版本优先优化的是 **结构清晰、成本可控、历史可恢复**；下一阶段应该是在不放弃轻量化的前提下，继续提升专家质量。

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
├── experts/              # IQ Layer 内部的轻量稀疏 MoE 专家
│   ├── base.py
│   ├── registry.py
│   ├── action_expert.py
│   ├── memory_overlay.py
│   └── risk_overlay.py
├── services/             # 服务层
│   ├── eq_service.py     #   EQ 主导服务（初判 / 终判 / 专家计划）
│   ├── iq_service.py     #   IQ Layer 协调器（轻量 MoE + 工具推理）
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
