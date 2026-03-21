# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is a companion AI architecture built around a **single-task, batch-driven Brain / Executor / World Model / Reflection** design.

The system has one outward-facing self. `Brain` handles user-facing expression and planning, `Executor` handles async execution and tools, and `World Model` plus `Reflection` keep the relationship and task continuity over time.

Current docs:

- [docs/brain-executor-architecture.zh-CN.md](docs/brain-executor-architecture.zh-CN.md)
- [docs/brain-executor-single-task-architecture.zh-CN.md](docs/brain-executor-single-task-architecture.zh-CN.md)
- [docs/brain-system-prompt.zh-CN.md](docs/brain-system-prompt.zh-CN.md)
- [docs/brain-executor-refactor-plan.zh-CN.md](docs/brain-executor-refactor-plan.zh-CN.md)

Archived historical docs:

- [docs/companion-left-right-brain-architecture.zh-CN.md](docs/companion-left-right-brain-architecture.zh-CN.md)
- [docs/companion-left-right-brain-module-contracts.zh-CN.md](docs/companion-left-right-brain-module-contracts.zh-CN.md)
- [docs/companion-protocol-spec.zh-CN.md](docs/companion-protocol-spec.zh-CN.md)

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
      "executorMode": {
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

Current gateway integrations are WebSocket/polling based and do not require a local listening port. The `gateway.port` setting is reserved for future webhook-style channels.

---

## Architecture

The current runtime flow is:

```text
User / Channel
  -> ConversationGateway
  -> RuntimeKernel
  -> PriorityPubSubBus
  -> InputNormalizer
  -> SessionRuntime
  -> BrainRuntime
  -> ExecutorRuntime (optional, current batch only)
  -> OutputRuntime
  -> DeliveryRuntime
  -> User / Channel

BrainRuntime / SessionRuntime / ExecutorRuntime
  -> World Model
BrainRuntime
  -> Reflection
```

### Canonical Principles

- The system exposes one continuous subject to the user.
- There is only one `current_task` per session.
- One brain turn can emit at most one `execute` action.
- Parallelism exists only inside `current_task.current_checks`.
- `Brain` owns user-facing expression, stable mainline, and next-batch decisions.
- `Executor` owns async execution, tools, and terminal fact reporting.
- `World Model` is the source of truth for current task state.
- `Reflection` is sidecar work triggered by `Brain`, not by `Executor`.

### Canonical Interaction Modes

| Input | Delivery | Typical Use |
|------|------|------|
| `turn` | `inline` | One-shot Q&A |
| `turn` | `push` | One-shot request with async notification |
| `turn` | `stream` | SSE text streaming for one turn |
| `stream` | `stream` | Real-time full-duplex dialogue |
| `stream` | `push` | Long-running background work after live conversation |

### Canonical Execution Rule

- `Executor` is the only execution system.
- `execute` means "submit the current batch to Executor for handling".
- `Executor` only emits terminal results for the current job.
- `SessionRuntime` writes those facts back into the world model.
- `Brain` re-decides only after a user event or the current batch reaches terminal state.

---

### Background Processes

Current background behavior is split across a daemon plus shared services:

#### SubconsciousDaemon
Three concurrent `asyncio.Task` loops:

| Loop | Interval | Behaviour |
|------|----------|-----------|
| `_decay_loop` | 30 min (configurable) | Gradually decays PAD drive values toward neutral |
| `_reflection_loop` | 1 h (configurable) | Triggers periodic `deep_reflection` through `RuntimeHost.run_deep_reflection()` |
| `_proactive_loop` | 10 min (configurable) | Randomly initiates a message to the user when idle |

#### Deep Reflection
Called by the subconscious reflection loop. It runs `deep_reflection` with a periodic signal and may:

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

`brain` and `executor` can use different models through `agents.defaults.brainMode` and `agents.defaults.executorMode`.

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
docker run -v ~/.emoticorebot:/root/.emoticorebot emoticorebot gateway
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
├── adapters/            # Conversation gateway adapters
├── background/          # Subconscious daemon and heartbeat loops
├── bootstrap.py         # RuntimeHost and top-level assembly
├── brain/               # Brain prompt, packet parsing, decision runtime
├── bus/                 # Priority event bus and routing
├── channels/            # Telegram / Discord / Slack / etc.
├── cli/                 # CLI entrypoints
├── config/              # Config loading and schema
├── context/             # Brain context assembly
├── cron/                # Scheduler service
├── delivery/            # Final outbound delivery
├── executor/            # Batch execution runtime and tool agent
├── input/               # Input normalization
├── memory/              # Long-term store, retrieval, vector mirror
├── models/              # Shared runtime models
├── output/              # Output event shaping
├── protocol/            # Typed runtime commands / events / topics
├── providers/           # LLM and transcription providers
├── reflection/          # Turn/deep reflection and governor
├── runtime/             # RuntimeKernel and transport bus
├── session/             # Session flow, thread store, world-model wakeups
├── skills/              # Built-in workflow skills
├── templates/           # Workspace bootstrap templates
├── tools/               # Built-in tools and MCP registry
├── utils/               # Shared helpers
└── world_model/         # Single-task schema, reducers, persistence
```

---

## Community

See `COMMUNICATION.md`.

## License

MIT.
