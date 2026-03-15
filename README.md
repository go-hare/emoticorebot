# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is a personal AI assistant built around a **brain + runtime + agent team** architecture.

`brain` remains the only outward-facing subject. A typed runtime owns task state and routing, internal agents execute delegated work, and reflection keeps memory/persona evolving after the first reply.

Detailed architecture design:

- [docs/final-brain-runtime-architecture.zh-CN.md](docs/final-brain-runtime-architecture.zh-CN.md)

---

## Install

Install from source (recommended for contributors):

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

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

emoticorebot v3 is organized around a typed, bus-driven runtime:

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

Only `ExecutiveBrain` is allowed to decide the user-facing reply. Internal agents are execution roles, not separate personas.

`TransportBus` is only the channel I/O bridge. The actual internal business event bus is still `PriorityPubSubBus`.

### Core Responsibilities

| Component | Role |
|------|------|
| `ExecutiveBrain` | Interprets the turn, decides whether to reply directly or create/resume/cancel a task, and owns the final user-facing wording. |
| `RuntimeKernel` | Owns task lifecycle, scheduling, assignment, recovery, and canonical state transitions. |
| `PriorityPubSubBus` | Typed event bus with priority routing, fan-out, and interceptor support. |
| `AgentTeam` | Registers `planner`, `worker`, and `reviewer` as internal execution roles. |
| `SafetyGuard` | Reviews reply drafts and sensitive task results before delivery. |
| `DeliveryService` | The only component that actually delivers replies to channels. |
| `MemoryGovernor` | Consumes post-turn signals for reflection, persona updates, and durable memory writes. |
| `ThreadStore` | Persists user-visible dialogue plus compact internal records. |

### Turn Flow

1. Channel input first enters `TransportBus`; `ConversationGateway` then bridges it into internal `input.event.user_message`.
2. `ExecutiveBrain` emits either `brain.command.reply`, `brain.command.ask_user`, or a task command such as `brain.command.create_task` / `brain.command.resume_task`.
3. `RuntimeKernel` persists the task, updates state, and publishes `runtime.command.assign_agent` or `runtime.command.resume_agent`.
4. `planner`, `worker`, and `reviewer` publish typed `task.report.*` events; runtime converts them into canonical `task.event.*` snapshots.
5. `RuntimeService` converts `brain.command.reply` / `brain.command.ask_user` into `output.event.reply_ready`.
6. `SafetyGuard` intercepts that draft and either approves, redacts, or blocks it before `DeliveryService` sends anything.
7. After delivery, reflection and memory signals run asynchronously and update long-term state.

### Agent Team

- Simple tasks usually go straight to `worker`.
- Higher-complexity tasks can go `planner -> worker`.
- High-risk or high-value outputs can additionally go through `reviewer`.
- The current `worker` implementation is backed by `DeepAgentExecutor`, but that is an execution detail. The outer contract is role-based and bus-driven.

### Persistence

| Layer | File / Store | Purpose |
|-------|------|---------|
| `dialogue` | `sessions/<session_key>/dialogue.jsonl` | User-visible conversation history |
| `internal` | `sessions/<session_key>/internal.jsonl` | Compact turn decisions, task summaries, and runtime facts |
| `checkpointer` | `sessions/_checkpoints/worker.pkl` | Worker execution resume state |
| `memory` | `memory/memory.jsonl` | Unified long-term memory source of truth |
| `vector mirror` | local vector index | Retrieval mirror of `memory.jsonl`, not the canonical source |
| `skills` | `skills/<name>/SKILL.md` | Reusable workflow knowledge and execution guidance |

### Current Constraints

- `brain` is still the only long-term-memory retriever.
- Internal agents do not speak directly to the user.
- Safety currently focuses on reply/output filtering and leaves embodied actuation as a future layer.

---

### Background Processes

Current background behavior is split across a daemon plus shared services:

#### SubconsciousDaemon
Three concurrent `asyncio.Task` loops:

| Loop | Interval | Behaviour |
|------|----------|-----------|
| `_decay_loop` | 30 min (configurable) | Gradually decays PAD drive values toward neutral |
| `_reflect_loop` | 1 h (configurable) | Triggers periodic `deep_reflection` through `ReflectionEngine` |
| `_proactive_loop` | 10 min (configurable) | Randomly initiates a message to the user when idle |

#### ReflectionEngine (meta-cognition)
Called by the subconscious reflect loop. It runs `deep_reflection` with a periodic signal and may:

- append stable memories into `memory/memory.jsonl`
- rewrite `SOUL.md` when a stable self-pattern is confirmed
- rewrite `USER.md` when a stable user-pattern is confirmed

`SOUL.md` and `USER.md` updates are validated before write, and writes stay atomic.

#### HeartbeatService
Two-phase background task checker:

1. **Phase 1 (decide)**: LLM reads `HEARTBEAT.md` and calls `heartbeat({action: "skip"|"run"})`.
2. **Phase 2 (execute)**: Only if `run`, invokes the registered `on_execute` callback.

---

### Tools

Built-in tools available to the execution layer:

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

All channels emit `InboundMessage` objects onto `TransportBus` and receive `OutboundMessage` back from `DeliveryService`. Internal typed events still run on `PriorityPubSubBus`.

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

`brain` and `worker` can use different models through `agents.defaults.brainMode` and `agents.defaults.workerMode`.

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
├── adapters/            # Conversation gateway / outbound dispatch
├── agent/
│   ├── context.py       # Brain prompt and memory context builder
│   ├── reflection/      # Reflection coordination and memory persistence
│   └── tool/            # Tool registry / execution wiring
├── background/          # Background daemon + periodic reflection entrypoints
├── bootstrap.py         # RuntimeHost, top-level assembly host
├── brain/
│   ├── decision_packet.py
│   ├── dialogue_policy.py
│   ├── executive.py
│   ├── reply_builder.py
│   └── task_policy.py
├── bus/
│   ├── interceptor.py
│   ├── priority_queue.py
│   ├── pubsub.py
│   └── router.py
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
├── protocol/            # Typed runtime submissions / events / task results
│   ├── commands.py
│   ├── envelope.py
│   ├── events.py
│   ├── memory_models.py
│   ├── safety_models.py
│   ├── task_models.py
│   ├── task_result.py
│   └── topics.py
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
├── tools/
├── channels/
├── providers/
├── cron/
├── models/
├── config/
├── skills/
├── templates/
├── utils/
└── cli/
```

---

## Community

See `COMMUNICATION.md`.

## License

MIT.
