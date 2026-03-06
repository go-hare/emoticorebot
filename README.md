# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is an ultra-lightweight personal AI assistant with a **fusion pipeline architecture (IQ + EQ)**, built on [LangGraph](https://github.com/langchain-ai/langgraph) and derived from the original Nanobot project.

It perceives emotional context in every conversation turn, dynamically balances factual reasoning (IQ) with empathetic expression (EQ), and continuously evolves its persona through background reflection.

---

## Install

Install from source (recommended for contributors):

```bash
git clone https://github.com/HKUDS/emoticorebot.git
cd emoticorebot
pip install -e .
```

Install from PyPI:

```bash
pip install emoticorebot-ai
```

> Requires Python ≥ 3.11. Optional Matrix E2EE support: `pip install "emoticorebot-ai[matrix]"`

---

## Quick Start

**1. Initialize local workspace:**

```bash
emoticorebot onboard
```

This creates `~/.emoticorebot/` with default config, `SOUL.md` (persona), `USER.md` (user profile), and `HEARTBEAT.md` (background task queue).

**2. Configure `~/.emoticorebot/config.json` (minimum):**

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

**3. Chat in CLI:**

```bash
emoticorebot agent
```

**4. Run gateway for chat channels:**

```bash
emoticorebot gateway
```

---

## Architecture

### Fusion Pipeline (LangGraph)

emoticorebot uses a **LangGraph state machine** to execute each conversation turn. The graph has three nodes and a dynamic router:

```
User Input
    │
    ▼
[SignalExtractor]  ──→  TurnSignals (task_strength, emotion_intensity,
    │                               relationship_need, urgency, safety_risk)
    ▼
[PolicyEngine]     ──→  FusionPolicy (iq_weight, eq_weight, empathy_depth,
    │                                 fact_depth, tool_budget, tone)
    ▼
 ┌──────────────────────────────────────────────────────┐
 │                  LangGraph Graph                     │
 │                                                      │
 │   ENTRY ──→ [EQ Node] ──┬──→ [IQ Node] ──┐          │
 │               ▲         │        │        │          │
 │               └─────────┘        ▼        │          │
 │                          [Memory Node] ←──┘          │
 │                                │                     │
 └────────────────────────────────┼─────────────────────┘
                                  ▼
                               END / Output
```

**Node responsibilities:**

| Node | Role |
|------|------|
| `EQ Node` | Empathy detection, emotion parsing, response style rendering. Decides whether to delegate to IQ. |
| `IQ Node` | Factual reasoning, tool execution (web search, file ops, code exec, MCP). |
| `Memory Node` | Writes conversation to semantic / relational / affective stores; saves PAD emotion state. |

**Routing logic (`FusionRouter`):**

- `EQ → IQ`: EQ detects a task that needs factual execution.
- `IQ → EQ`: IQ has results; EQ wraps them in empathetic language.
- `* → Memory`: `done=True` or IQ attempt limit reached; write-back and exit.

---

### Signal & Policy Layer

`SignalExtractor` parses each user turn into five float signals `[0, 1]`:

| Signal | Meaning |
|--------|---------|
| `task_strength` | Presence of action keywords ("查询", "run", "fix", …) |
| `emotion_intensity` | Emotional keywords + exclamation density |
| `relationship_need` | Derived from emotion + "你" pronoun presence |
| `urgency` | Urgency keywords ("立刻", "asap", …) + question marks |
| `safety_risk` | Hard-coded crisis detection (self-harm phrases → 1.0) |

`PolicyEngine` converts signals into a `FusionPolicy`:

| Policy Field | Effect |
|---|---|
| `iq_weight / eq_weight` | Balance between factual and empathetic processing |
| `empathy_depth` | 0 = none, 1 = light, 2 = deep empathy opening |
| `fact_depth` | IQ reasoning depth (1–3) |
| `tool_budget` | Max tool calls per turn (3–6) |
| `tone` | `professional` / `warm` / `balanced` / `concise` |

Runtime adjustments from `ReflectionEngine` can bias the policy via `eq_bias`, `iq_bias`, and `tone_preference`.

---

### Memory Layer

All memory is stored as files under `~/.emoticorebot/data/` (or the configured workspace):

| Store | File | Purpose |
|-------|------|---------|
| `SemanticStore` | `semantic_memories.jsonl` | Factual notes with tags and importance scores |
| `RelationalStore` | `relational_memories.jsonl` | Preferences, relationships, warm memories |
| `AffectiveStore` | `affective_traces.jsonl` | PAD (Pleasure / Arousal / Dominance) emotional timeline |
| `PolicyStateStore` | `policy_state.json` | Active runtime policy adjustments with TTL |
| `MemoryFacade` | — | Unified read/write API for all stores |

The **PAD model** (Pleasure-Arousal-Dominance) is used to track the bot's continuous emotional state across sessions. It is loaded at startup from `current_state.md` and written back after every turn.

---

### Background Processes

Three async daemons run independently in the background:

#### SubconsciousDaemon
Three concurrent `asyncio.Task` loops:

| Loop | Interval | Behaviour |
|------|----------|-----------|
| `_decay_loop` | 30 min (configurable) | Gradually decays PAD drive values toward neutral |
| `_reflect_loop` | 1 h (configurable) | Triggers `ReflectionEngine` to update SOUL/USER |
| `_proactive_loop` | 10 min (configurable) | Randomly initiates a message to the user when idle |

#### ReflectionEngine (meta-cognition)
Reads recent relational memories and calls an LLM to produce structured JSON:

- **`soul_update`** — micro-adjusts `SOUL.md` (persona evolution, anchors preserved)
- **`user_update`** — appends new user insights to `USER.md`
- **`policy_adjustment`** — sets `eq_bias`, `iq_bias`, `tone_preference`, `tool_budget_delta`, `duration_hours`

Both `SOUL.md` and `USER.md` updates go through a **validator** before write. Writes are atomic (temp file → rename, with backup).

#### HeartbeatService
Two-phase background task checker:

1. **Phase 1 (decide)**: LLM reads `HEARTBEAT.md` and calls `heartbeat({action: "skip"|"run"})`.
2. **Phase 2 (execute)**: Only if `run`, invokes the registered `on_execute` callback.

---

### Tools

Built-in tools available to the IQ node:

| Tool | Description |
|------|-------------|
| `web_search` | Brave Search API integration |
| `web_fetch` | Fetch and parse web page content (readability) |
| `exec` | Execute shell commands or code snippets |
| `read_file` / `write_file` | File system read/write |
| `list_dir` | Directory listing |
| `system_info` | OS / environment metadata |
| MCP tools | Any tool exposed via a configured MCP server |

---

### Skills

Skills are Markdown-based prompt plugins loaded at runtime from `~/.emoticorebot/skills/` or `emoticorebot/skills/`:

| Skill | Purpose |
|-------|---------|
| `cron` | Schedule recurring or one-off tasks |
| `memory` | Explicit memory management commands |
| `github` | GitHub API interactions |
| `clawhub` | ClawHub integration |
| `summarize` | Document/URL summarization |
| `tmux` | tmux session automation |
| `weather` | Weather queries |
| `skill-creator` | Bootstrap new skills |

---

## Channels

Supported messaging channels (configured under `channels` in `config.json`):

| Channel | Notes |
|---------|-------|
| Telegram | Bot token via `@BotFather`; proxy support |
| Discord | Gateway WebSocket; intents configurable |
| WhatsApp | Requires `bridge/` Node.js bridge |
| Feishu (Lark) | WebSocket long connection |
| DingTalk | Stream mode |
| Slack | Slack SDK; Markdown conversion |
| Email | IMAP (inbound) + SMTP (outbound) |
| QQ | qq-botpy |
| Matrix | nio; optional E2EE |
| Mochat | Socket.IO |

All channels emit `InboundMessage` events onto the `MessageBus` and receive `OutboundMessage` from the runtime.

---

## MCP (Model Context Protocol)

Connect any MCP server via config:

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

## LLM Providers

emoticorebot uses **LangChain** adapters and **litellm** for broad model support:

| Provider | Package |
|----------|---------|
| OpenAI / OpenRouter | `langchain-openai` |
| Anthropic (Claude) | `langchain-anthropic` |
| Google Gemini | `langchain-google-genai` |
| Groq | `langchain-groq` |
| Ollama (local) | `langchain-ollama` |

IQ and EQ can each use a **different model** (`iq.model` / `eq.model` in config), enabling cost/quality trade-offs.

---

## Cron / Scheduler

Schedule tasks with three modes:

```json
{
  "kind": "cron",  "expr": "0 9 * * *", "tz": "Asia/Shanghai"
}
{
  "kind": "every", "every_ms": 3600000
}
{
  "kind": "at",    "at_ms": 1700000000000
}
```

CLI management:

```bash
emoticorebot cron list
```

---

## Security

For production, restrict tool execution to the workspace:

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

Or with Docker Compose:

```bash
docker-compose up
```

---

## CLI Commands

```bash
emoticorebot onboard          # Initialize workspace
emoticorebot agent            # Interactive CLI chat
emoticorebot gateway          # Start gateway (all enabled channels)
emoticorebot status           # Show runtime status
emoticorebot cron list        # List scheduled tasks
emoticorebot channels status  # Show channel connection status
```

---

## Project Structure

```text
emoticorebot/
├── core/                 # Fusion orchestration (LangGraph graph, nodes, router, policy)
│   ├── graph.py          #   LangGraph graph definition & compilation
│   ├── state.py          #   FusionState / IQState / EQState
│   ├── signal_extractor.py  # TurnSignals extraction
│   ├── policy_engine.py  #   FusionPolicy generation
│   ├── router.py         #   FusionRouter (node routing logic)
│   ├── model.py          #   LLMFactory (multi-provider)
│   ├── mcp.py            #   MCP client integration
│   ├── skills.py         #   Skill loader
│   ├── context.py        #   Prompt context builder
│   └── nodes/            #   eq_node / iq_node / memory_node
├── services/             # Service layer
│   ├── eq_service.py     #   EQ service (empathy rendering)
│   ├── iq_service.py     #   IQ service (tool-augmented reasoning)
│   ├── memory_service.py #   Memory read/write service
│   └── tool_manager.py   #   Tool registry & execution
├── memory/               # Layered memory stores
│   ├── semantic_store.py
│   ├── relational_store.py
│   ├── affective_store.py
│   ├── policy_state_store.py
│   └── memory_facade.py
├── background/           # Background daemons
│   ├── subconscious.py   #   SubconsciousDaemon (decay / reflect / proactive)
│   ├── reflection.py     #   ReflectionEngine (meta-cognition)
│   ├── heartbeat.py      #   HeartbeatService (two-phase task runner)
│   └── subagent.py       #   Background sub-agent execution
├── tools/                # Built-in tool implementations
├── channels/             # Channel adapters (Telegram, Discord, …)
├── providers/            # LLM provider utilities
├── runtime/              # FusionRuntime (dispatch + orchestration)
├── bus/                  # MessageBus (inbound/outbound event queue)
├── cron/                 # Cron scheduler service
├── session/              # Session management
├── models/               # Shared data models (EmotionState, …)
├── config/               # Pydantic config schema
├── skills/               # Built-in skill definitions (Markdown)
├── templates/            # Onboarding file templates
├── utils/                # Shared utilities
└── cli/                  # CLI entrypoints (Typer)
```

---

## Community

See `COMMUNICATION.md`.

## License

MIT.
