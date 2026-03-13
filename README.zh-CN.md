# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** 是一个轻量级个人 AI 助手，采用 **`Brain → Task System → Central`** 三层架构。

- **Brain**：通过 LangGraph Agent 管理对话和任务（create_task/fill_task/cancel_task/query_task）
- **Task System**：并发任务队列，支持进度上报、补充信息请求、异步执行
- **Central**：基于 Deep Agents 的执行引擎，负责工具调用与复杂任务执行
- **Reflection**：异步反思系统，每轮 `turn_reflection` + 按需 `deep_reflection`

详细设计文档：

- [docs/ARCHITECTURE.zh-CN.md](docs/ARCHITECTURE.zh-CN.md)
- [docs/FIELDS.zh-CN.md](docs/FIELDS.zh-CN.md)

**核心特性：**

✅ **工具化任务管理**：Brain 通过工具（而非直接调用）创建和管理任务  
✅ **异步并发执行**：支持多任务并发，进度上报，补充信息请求  
✅ **完整字段传递**：路由信息（channel/chat_id）、执行信息（execution_summary）贯穿调用链  
✅ **智能反思系统**：每轮 turn_reflection + 按需 deep_reflection，自动更新 USER.md/SOUL.md  
✅ **PAD 情感模型**：Pleasure-Arousal-Dominance 三维情绪追踪

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

### 核心设计：`brain -> task system -> central`

emoticorebot 采用**三层架构**，从上到下依次为：

```
┌─────────────────────────────────────────────────────────────┐
│                         用户输入                              │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Runtime（消息调度 + 会话管理 + 反思协调）                      │
│  - 接收/发送消息                                              │
│  - 管理 session（dialogue.jsonl / internal.jsonl）           │
│  - 异步调度 turn_reflection + deep_reflection                │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Brain（LangGraph Agent + 任务管理工具）                       │
│  - 理解用户意图                                               │
│  - 决策：直接回复 OR 创建任务                                  │
│  - 工具：create_task / fill_task / cancel_task / query_task  │
│  - 输出：{"message": "...", "execution_summary": "..."}      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Task System（任务队列 + 事件路由）                            │
│  - 管理并发任务（running / waiting_input / done / failed）    │
│  - 传递路由信息（channel / chat_id）                          │
│  - 事件回调：progress / need_input / done / failed            │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Central（Deep Agents 执行引擎）                              │
│  - 自主规划与工具调用循环                                      │
│  - 上报进度：report_progress()                                │
│  - 请求补充：request_input(field, question)                   │
│  - 返回结果摘要                                               │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼ （任务事件回调）
┌─────────────────────────────────────────────────────────────┐
│  Brain 接收任务事件并转换为自然语言回复用户                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  异步反思（不阻塞用户响应）                                    │
│  - cognitive_event 提炼                                       │
│  - turn_reflection（每轮必做）                                │
│  - deep_reflection（按需触发）                                │
│  - 更新 USER.md / SOUL.md / memory                           │
└─────────────────────────────────────────────────────────────┘
```

**核心组件职责：**

| 组件 | 职责 |
|------|------|
| `Runtime` | 消息调度、会话管理、反思协调 |
| `Brain` | 唯一对外主体。通过工具管理任务（create_task/fill_task/cancel_task/query_task） |
| `Task System` | 任务队列管理、事件路由、状态追踪 |
| `Central` | Deep Agents 执行引擎。规划、工具调用、技能复用 |
| `Reflection` | 异步后处理。每轮 `turn_reflection`，按需 `deep_reflection` |

**关键特性：**

1. **Brain 通过工具创建任务**：`create_task(task_description, task_title)` 将任务委托给 Task System
2. **Task System 管理并发**：支持任务队列、状态追踪、等待用户输入
3. **路由信息传递**：`channel` 和 `chat_id` 从 Brain → Task System → Central → 事件回调
4. **异步任务回复**：Central 执行期间可随时向用户报告进度、请求信息
5. **统一反思入口**：用户消息和任务事件都触发 `turn_reflection`

### Deep Agents 执行模型

`Central` 使用 **Deep Agents** 作为执行引擎：

| 组件 | 职责 |
|---|---|
| Deep Agents | LangGraph 构建的 Agent，支持自主工具调用循环 |
| Planner | 内部任务分解与执行路径规划 |
| Tools | 文件操作、Shell、web search、MCP 等注册能力 |
| Skills | 复用工作区 `skills/` 下的 Markdown 工作流说明 |

**执行流程：**

1. **Task System** 接收 Brain 创建的任务
2. **Central** 通过 Deep Agents 自主执行（内部循环）
3. **进度上报**：通过 `task.report_progress()` 发送中间进度
4. **请求补充**：通过 `task.request_input()` 请求用户提供信息
5. **完成通知**：任务完成/失败后发送事件给 Runtime
6. **Brain 处理事件**：将任务事件转换为自然语言回复用户

### 典型工作流示例

#### 1. 普通请求 → 委托执行

示例：“帮我总结一下这个文件。”

```text
用户输入
  → brain 判断需要 central 帮忙
  → brain 下发明确内部任务
  → central 规划并执行
  → brain 完成最终对外回复
```

特点：

- `brain` 到 `central` 的直接交接
- 不把原始工具输出直接暴露给用户
- 最终语气仍由 `brain` 收口

#### 2. 历史恢复 / 续聊 → 承接上下文并恢复执行

示例：

- 上一轮：“帮我查一下天气”
- assistant：“哪个城市？”
- 用户：“上海”

```text
用户补充输入
  → brain 判断大概率是在恢复待续任务
  → brain 从 session、internal 和暂停 task 状态中恢复承接线索
  → central 带着恢复后的上下文继续执行任务
  → brain 审核融合结果并自然地接上回复
```

特点：

- 适合未完成任务恢复
- 跨轮承接主要由 `brain` 负责
- 在上下文足够时，尽量避免重复追问同一个缺参

#### 3. 敏感 / 低置信度请求 → central 分析 + brain 守门

示例：“执行这个命令，然后把旧文件删掉。”

```text
用户请求
  → brain 判断可能涉及外部动作或更高风险
  → brain 下发更谨慎的内部任务
  → central 评估可执行性、风险与保护条件
  → brain 决定是谨慎回答、追问补充信息，还是继续内部讨论
```

特点：

- 适合安全敏感场景和过度自信抑制
- 在涉及工具或低置信度时尤其有价值
- 最终仍然保持统一的 `brain` 说话风格

---

### 提示词构建与上下文管理

**Brain 提示词构建**（`ContextBuilder.build_brain_system_prompt`）

- 从 `AGENTS.md` 载入 Brain 行为规则
- 从 `SOUL.md` 载入人格定义
- 从 `USER.md` 载入用户认知
- 从 `current_state.md` 载入 PAD 情绪状态
- 检索最近的 `cognitive_event` 作为上下文
- 提供任务管理工具（create_task/fill_task/cancel_task/query_task）
- Brain 通过 JSON 输出：`{"message": "...", "execution_summary": "..."}`

**Central 执行上下文**

- 接收来自 Task System 的任务参数
- 使用 Deep Agents 自主循环执行
- 通过 `task.report_progress()` 上报进度
- 通过 `task.request_input()` 请求补充信息
- 返回执行结果摘要

**会话与路由信息**

- `channel` 和 `chat_id`：从用户消息 → Brain → Task System → Central → 事件回调
- `session_id`：会话标识，用于管理对话历史和任务队列
- `message_id`：消息唯一标识，用于追踪和关联

---

### 记忆与反思系统

**分层存储结构：**

| 层级 | 文件路径 | 用途 |
|--------|------|------|
| 对话历史 | `sessions/<session_id>/dialogue.jsonl` | 用户可见的对话记录 |
| 内部消息 | `sessions/<session_id>/internal.jsonl` | Brain 内部推理记录 |
| 认知事件 | `memory/cognitive_events.jsonl` | 每轮提炼的结构化认知切片 |
| 长期记忆 | `memory/self_memory.jsonl` | 稳定的自我模式 |
| 关系记忆 | `memory/relation_memory.jsonl` | 用户认知与关系模式 |
| 洞察记忆 | `memory/insight_memory.jsonl` | 执行模式与技能候选 |
| 人格锚点 | `SOUL.md` | 人格定义（由 deep_reflection 更新） |
| 用户档案 | `USER.md` | 用户认知（由 deep_reflection 更新） |

**反思流程：**

```
每轮对话/任务事件
    │
    ▼
写入 dialogue.jsonl + internal.jsonl
    │
    ▼
提炼 cognitive_event
    │
    ▼
turn_reflection（必做）
    ├─→ 生成轮次总结
    ├─→ 评估执行效果
    ├─→ 提取 memory_candidates
    ├─→ 更新 USER.md / SOUL.md（高置信）
    └─→ 微调 PAD 情绪状态
    │
    ▼
判断是否需要 deep_reflection
    │
    ▼
deep_reflection（按需触发）
    ├─→ 分析多轮认知事件
    ├─→ 提炼稳定模式
    ├─→ 更新长期记忆
    └─→ 生成技能候选
```

**触发 deep_reflection 的条件：**

1. 任务执行失败或需要更多信息
2. 高重要性对话 + 身份信息更新
3. Brain 发出特殊信号（如循环限制达到）
4. 周期性触发（由 SubconsciousDaemon 调度）

**PAD 情感模型**（Pleasure-Arousal-Dominance）：

- 从 `current_state.md` 加载当前情绪状态
- 每轮对话后根据交互内容自动更新
- `turn_reflection` 可微调 PAD 增量（±0.3）
- 后台进程定期衰减回中性值

---

### 当前架构特点

**优势：**

1. **清晰的职责分层**
   - Runtime：消息调度与会话管理
   - Brain：决策与任务管理
   - Task System：并发任务队列
   - Central：工具执行引擎

2. **异步任务执行**
   - 任务在后台并发执行
   - 支持进度上报和补充信息请求
   - Brain 可管理多个并发任务

3. **完整的字段传递**
   - 路由信息（channel/chat_id）贯穿整个调用链
   - 执行信息（execution_summary）准确传递给反思层
   - 状态字段（status/missing/failure_reason）完整保留

4. **灵活的反思机制**
   - 每轮必做 turn_reflection（快速、轻量）
   - 按需触发 deep_reflection（深度、周期性）
   - 自动更新 USER.md / SOUL.md

5. **工具化的任务管理**
   - Brain 通过工具（而非直接调用）管理任务
   - 支持创建、填充、取消、查询任务
   - 任务状态透明可追踪

**设计权衡：**

- **Brain 不直接调用 Central**：通过 Task System 隔离，支持并发和异步
- **任务事件回调**：Central 执行期间可随时通知用户，而非阻塞等待
- **反思异步化**：不阻塞用户响应，保证首响速度
- **历史传递保守**：Central 当前不接收完整对话历史（可按需启用）

### 路线图

**近期优化方向：**

1. **增强 Central 历史传递**
   - 从 Brain 传递最近对话历史给 Central
   - 让 Central 更好地理解用户意图和上下文

2. **任务恢复能力增强**
   - 识别隐式续聊（用户补充信息但未明确说明）
   - 更智能的任务状态恢复

3. **执行轨迹可观测性**
   - 保留更详细的 Central 执行日志
   - 提供调试和审计能力

4. **反思质量提升**
   - 增强 execution_review 的因果分析
   - 更准确的失败模式识别

5. **工具与技能扩展**
   - 增加更多内置工具
   - 简化自定义技能创建流程

**长期演进方向：**

- **多模态支持**：图像、语音、视频理解
- **主动学习**：从失败中自动提取技能
- **分布式执行**：支持远程 Central 节点
- **更强的规划能力**：长期任务分解与跟踪

---

### 后台进程

当前后台行为由一个守护进程加若干共享服务组成：

#### SubconsciousDaemon（潜意识守护进程）
三条并发 `asyncio.Task` 循环：

| 循环 | 默认间隔 | 行为 |
|------|---------|------|
| `_decay_loop` | 30 分钟 | 逐步衰减 PAD 驱动值趋向中性 |
| `_reflect_loop` | 1 小时 | 通过 `ReflectionEngine` 触发周期性 `deep_reflection` |
| `_proactive_loop` | 10 分钟 | 在空闲时随机向用户主动发起一条消息 |

#### ReflectionEngine（元认知反思引擎）
由潜意识反思循环调用。它会以周期信号运行 `deep_reflection`，并可能：

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

`central` 可调用的内置工具：

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

`brain` 和 `central` 可以分别配置**不同模型**，对应 `agents.defaults.brainMode` 与 `agents.defaults.centralMode`，从而灵活权衡成本与质量。

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
├── agent/                # Brain / Central / Reflection / Tools
│   ├── brain.py          #   BrainService（LangGraph Agent + 任务管理工具）
│   ├── context.py        #   提示词与记忆上下文构建
│   ├── model.py          #   LLMFactory（多模型支持）
│   ├── system.py         #   SessionTaskSystem（任务队列管理）
│   ├── central/
│   │   ├── central.py    #   CentralAgentService（Deep Agents 执行引擎）
│   │   ├── backend.py    #   Deep Agents 后端集成
│   │   └── stream.py     #   流式输出处理
│   ├── reflection/
│   │   ├── coordinator.py #  ReflectionCoordinator（反思协调）
│   │   ├── turn.py        #  TurnReflectionService（逐轮反思）
│   │   ├── deep.py        #  DeepReflectionService（深度反思）
│   │   ├── memory.py      #  MemoryService（记忆持久化）
│   │   └── types.py       #  反思类型定义
│   └── tool/
│       ├── manager.py     #  ToolManager（工具注册与执行）
│       └── mcp.py         #  MCP 集成
├── runtime/              # 消息调度与会话管理
│   ├── event_bus.py      #   RuntimeEventBus（消息总线）
│   └── runtime.py        #   EmoticoreRuntime（主运行时）
├── memory/               # 分层记忆实现
│   ├── structured_stores.py
│   ├── stateful_stores.py
│   ├── retriever.py
│   └── memory_facade.py
├── background/           # 后台守护进程
│   ├── subconscious.py   #   SubconsciousDaemon（衰减/反思/主动对话）
│   ├── reflection.py     #   ReflectionEngine（周期性反思）
│   └── heartbeat.py      #   HeartbeatService（任务检查器）
├── models/               # 数据模型
│   ├── emotion_state.py  #   EmotionStateManager（PAD 模型）
│   └── cognitive.py      #   CognitiveEvent（认知事件）
├── session/              # 会话持久化
│   └── manager.py        #   SessionManager
├── channels/             # 渠道适配器
│   ├── telegram.py
│   ├── discord.py
│   ├── whatsapp.py
│   └── ...
├── tools/                # 内置工具
│   ├── exec.py
│   ├── file.py
│   ├── web.py
│   └── ...
├── cron/                 # 定时任务
├── config/               # 配置 Schema
├── skills/               # 内置技能
└── cli/                  # CLI 入口
```

---

## 社区

见 `COMMUNICATION.md`。

## 许可证

MIT。

