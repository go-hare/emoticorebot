# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is a companion AI architecture built around a **single subject with main_brain / execution / memory / reflection** design.

The system has one outward-facing self. `Main Brain` handles user-facing understanding, task decisions, and final expression. `Execution` handles async tool use and long-running work. `Memory` plus `Reflection` keep the relationship continuous over time.

Detailed architecture design:

- [docs/companion-main-brain-execution-architecture.zh-CN.md](docs/companion-main-brain-execution-architecture.zh-CN.md)
- [docs/companion-main-brain-execution-module-contracts.zh-CN.md](docs/companion-main-brain-execution-module-contracts.zh-CN.md)

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

The authoritative architecture is:

```text
User / Channel
  -> ConversationGateway
  -> RuntimeKernel
  -> InputNormalizer
  -> SessionRuntime
  -> MainBrainFrontLoop
  -> OutputRuntime
  -> DeliveryRuntime

MainBrainFrontLoop
  -> ExecutionRuntime (on demand)
ExecutionRuntime
  -> MainBrainFrontLoop

MainBrainFrontLoop / ExecutionRuntime
  -> Memory
MainBrainFrontLoop / ExecutionRuntime
  -> Reflection
```

### Canonical Principles

- The system exposes one continuous subject to the user.
- Inputs are modeled only as `turn` or `stream`.
- Deliveries are modeled only as `inline`, `push`, or `stream`.
- `Main Brain` owns user-facing expression, task decisions, and final replies.
- `Execution` owns async execution, tools, and long-running work.
- `Memory` and `Reflection` keep long-term continuity.

### Canonical Interaction Modes

| Input | Delivery | Typical Use |
|------|------|------|
| `turn` | `inline` | One-shot Q&A |
| `turn` | `push` | One-shot request with async notification |
| `turn` | `stream` | SSE text streaming for one turn |
| `stream` | `stream` | Real-time full-duplex dialogue |
| `stream` | `push` | Long-running background work after live conversation |

### Canonical Execution Rule

- `ExecutionRuntime` is the only background execution system.
- `create_task` means "submit a request to ExecutionRuntime for review and handling".
- `ExecutionRuntime` may `accept`, `answer_only`, or `reject`.
- User-visible wording still returns through `MainBrainFrontLoop`.

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

`main_brain` and `execution` can use different models through `agents.defaults.mainBrainMode` and `agents.defaults.executionMode`.

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
├── adapters/            # Conversation gateway / outbound dispatch
├── background/          # Background daemon + periodic reflection entrypoints
├── bootstrap.py         # RuntimeHost, top-level assembly host
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
├── bus/
│   ├── interceptor.py
│   ├── priority_queue.py
│   ├── pubsub.py
│   └── router.py
├── delivery/
│   ├── runtime.py
│   └── service.py
├── memory/
│   ├── crystallizer.py
│   ├── retrieval.py
│   ├── store.py
│   └── vector_index.py
├── input/
│   └── normalizer.py
├── output/
│   └── runtime.py
├── protocol/            # Typed runtime commands / events / models
│   ├── commands.py
│   ├── contracts.py
│   ├── envelope.py
│   ├── event_contracts.py
│   ├── events.py
│   ├── reflection_models.py
│   ├── priorities.py
│   ├── task_models.py
│   └── topics.py
├── runtime/
│   ├── kernel.py
│   └── transport_bus.py
├── safety/
│   └── guard.py
├── session/
│   ├── models.py
│   ├── runtime.py
│   ├── history_store.py
│   └── thread_store.py
├── tools/
│   ├── manager.py
│   └── mcp.py
├── channels/
├── providers/
│   └── factory.py
├── cron/
├── models/
├── config/
├── skills/
├── templates/
├── reflection/
│   ├── candidates.py
│   ├── cognitive.py
│   ├── deep.py
│   ├── governor.py
│   ├── input.py
│   ├── manager.py
│   ├── persona.py
│   ├── runtime.py
│   └── turn.py
├── utils/
└── cli/
```

---

## Community

See `COMMUNICATION.md`.

## License

MIT.

