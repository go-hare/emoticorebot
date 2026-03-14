# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is an ultra-lightweight personal AI assistant built around a **brain -> central** architecture and derived 

It keeps `brain` as the only outward-facing subject, delegates complex work to a **Deep Agents-based `central`** when needed, and evolves through `turn_reflection + deep_reflection`.

Detailed architecture design:

- [docs/non-compatible-runtime-refactor.md](docs/non-compatible-runtime-refactor.md)
- [docs/non-compatible-runtime-refactor.zh-CN.md](docs/non-compatible-runtime-refactor.zh-CN.md)

---

## Install

Install from source (recommended for contributors):

```bash
git clone https://github.com/go-hare/emoticorebot.git
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

### `brain -> central` Loop

emoticorebot now uses an **explicit turn loop** instead of an outer LangGraph state machine. The runtime stays simple: `brain` decides, `central` executes when needed, and reflection happens asynchronously after the user-facing response.

```
User Input
    │
    ▼
session / internal / checkpointer
    │
    ▼
brain ──→ central (optional)
    │             │
    └──────←──────┘
    │
    ▼
User Reply
    │
    ▼
cognitive_event -> turn_reflection -> deep_reflection -> memory
```

Only `brain` can terminate the turn and produce the user-facing message.

**Runtime responsibilities:**

| Component | Role |
|------|------|
| `brain` | Only subject. Interprets intent, controls central, preserves relationship continuity, and produces the final user-facing reply. |
| `RuntimeHost` | Top-level assembly host that wires gateway, brain, runtime, reflection, and background services together. |
| `central` | Deep Agents-based execution layer. Handles planning, tools, skills, and long-running complex tasks. |
| `reflection` | Async post-turn process. Produces `turn_reflection` every turn and `deep_reflection` on demand or by periodic signal. |

**Turn contract:**

- `brain -> central`: delegate only one clear internal request, plus resume metadata when a paused task should continue.
- `central -> brain`: return a compact packet with `control_state`, `status`, `analysis`, `risks`, `missing`, `recommended_action`, `confidence`, and optional `pending_review`.
- `brain -> user`: only `brain` can answer, ask for missing info, or decide to continue internal work.
- `post-turn reflection`: after the first user-facing reply, the runtime writes turn records, builds `cognitive_event`, runs `turn_reflection`, and schedules `deep_reflection` only when warranted.

### Deep Agents in `central`

The current `central` no longer uses the older expert-overlay pipeline. Instead, it is powered by Deep Agents while preserving the compact `brain -> central` contract.

| Component | Role |
|---|---|
| Planner | Breaks down the internal task and decides how to proceed |
| Tools | Executes registered capabilities such as file operations, shell execution, web search, fetch, messaging, and cron |
| Step-level concurrency | Runs independent tool or analysis steps in parallel when safe |
| Skills | Reuses local workflow instructions from the workspace `skills/` directory |

The contract remains **brain-led**:

- `brain` decides whether `central` is needed and what internal task should be delegated
- `central` plans and executes using tools, skills, and step-level concurrency
- `central` returns a normalized packet with `control_state`, `status`, `analysis`, `risks`, `missing`, `recommended_action`, and `confidence`
- `brain` decides whether to answer, ask the user, or continue internal deliberation

This keeps the outer loop stable while allowing the inner execution kernel to become more capable over time.

### Typical Workflows

#### 1. Normal request → delegated execution

Example: “Help me summarize this file.”

```text
User
  → brain decides central help is useful
  → brain delegates one concrete internal request
  → central plans and executes it
  → brain finalizes the user-facing reply
```

Properties:

- simple handoff from `brain` to `central`
- no raw tool output is exposed directly to the user
- `brain` still controls the final wording

#### 2. Resume / follow-up request → continuity + resumed execution

Example:

- previous turn: “Check the weather for me.”
- assistant: “Which city?”
- user: “Shanghai.”

```text
User follow-up
  → brain detects likely pending-task recovery
  → brain reconstructs continuity from session, internal history, and paused task state
  → central continues the delegated task with the recovered context
  → brain reviews the merged result and answers naturally
```

Properties:

- optimized for unfinished-task recovery
- keeps cross-turn continuity on the `brain` side
- avoids asking the same missing question again when enough context exists

#### 3. Sensitive / low-confidence request → central analysis + brain safeguard

Example: “Run this command and delete the old files.”

```text
User request
  → brain detects possible external action / higher risk
  → brain delegates a cautious internal task
  → central evaluates feasibility, risks, and missing safeguards
  → brain either answers conservatively, asks the user first, or continues internal deliberation
```

Properties:

- optimized for safety and overconfidence control
- useful when tools are involved or confidence is low
- keeps the user-facing voice unified through `brain`

---

### Decision Inputs & Prompt Construction

The current implementation no longer depends on an outer router layer. Turn planning comes directly from `brain`, session state, and the central packet.

**`brain` prompt construction (`ContextBuilder.build_brain_system_prompt`)**

- loads `brain` rules from workspace `AGENTS.md`
- loads persona anchors from `SOUL.md` and user cognition from `USER.md`
- loads `current_state.md` for PAD / state grounding
- retrieves recent `cognitive_event` context
- asks `brain` to decide whether to answer directly or delegate to `central`

**`central` prompt construction (`emoticorebot.execution.backend.build_agent_instructions`)**

- enforces the `brain -> central` contract
- injects workspace / builtin skill routes and skill summaries
- constrains output to the compact central packet
- uses tool registry, Deep Agents backend routing, and checkpointer-backed resume state

**Session and continuation inputs**

- `dialogue.jsonl` preserves user-visible conversation history
- `internal.jsonl` preserves compact `brain <-> central` summaries and control decisions
- paused task metadata carries `thread_id`, `run_id`, `missing`, and `pending_review`
- checkpointer state lets `central` resume from the previous interruption point

In short, the working loop is now: **history + cognitive context + paused task → `brain` planning → `central` execution (optional) → `brain` finalization**.

---

### Memory Layer

The architecture now separates **runtime material**, **cognitive events**, and **durable memory**:

| Layer | File / Store | Purpose |
|-------|------|---------|
| `session` | `sessions/<session_key>/dialogue.jsonl` | User-visible `user <-> brain` conversation |
| `internal` | `sessions/<session_key>/internal.jsonl` | Compact `brain <-> central` summaries, control actions, pause/resume hints |
| `checkpointer` | `sessions/_checkpoints/central.pkl` | Central pause / resume state |
| `cognitive_event` | `memory/cognitive_events.jsonl` | Structured per-turn slices built after the reply |
| `self_memory` | `memory/self_memory.jsonl` | Stable `brain` patterns |
| `relation_memory` | `memory/relation_memory.jsonl` | Stable user / relationship knowledge |
| `insight_memory` | `memory/insight_memory.jsonl` | Deep insights, durable execution patterns, skill candidates |

The runtime flow is:

1. Write `dialogue` and `internal` turn records.
2. Build `cognitive_event` from the completed turn.
3. Run `turn_reflection` after every turn.
4. Schedule `deep_reflection` only when `brain` judges the turn worth deeper consolidation, or when a periodic signal triggers reflection.
5. Write only stable conclusions into long-term memory, and optionally update `SOUL.md`, `USER.md`, or future `skills`.

The `central` only receives the delegated internal request plus resume metadata. It does not replay the entire user conversation; cross-turn continuity remains `brain`-led.

The **PAD model** (Pleasure-Arousal-Dominance) is used to track the bot's continuous emotional state across sessions. It is loaded at startup from `current_state.md` and written back after every turn.

---

### Current Limitations

The current architecture is already usable, but it is intentionally still conservative in a few places:

- Deep Agents output still needs normalization into a compact `central` packet, so richer intermediate traces are still mostly kept in runtime material.
- The current tool set is intentionally narrow; broader workspace and research coverage can still be added.
- Cross-turn continuity is still `brain`-centric and conservative; implicit follow-up recovery can become stronger.
- `deep_reflection` stores durable summaries rather than full raw execution traces.
- Skill promotion is still reflection-led and conservative instead of fully automatic.

### Roadmap

Recommended next steps for this architecture:

1. **Improve Deep Agents observability**
   - preserve richer execution traces without bloating session history
   - expose better debugging hooks for internal planning and execution

2. **Strengthen continuity recovery**
   - improve implicit follow-up detection
   - better reconcile paused task / memory / user follow-up signals

3. **Deepen reflection outputs**
   - enrich `turn_reflection.execution_review` and `deep_reflection` with stronger causal tags
   - let future turns retrieve not only the outcome but also the failure mode that triggered it

4. **Refactor the central internally**
   - split planning / execution / merging more clearly
   - preserve the current lightweight behavior while making extension easier

5. **Expand tools and skills carefully**
   - only after the current Deep Agents workflow is stable
   - examples: richer workspace helpers, stronger verification flows, domain-specific skills

In short: the current version optimizes for **clarity, controllable cost, and recoverable history**, and future work should improve central quality without adding another outer orchestration layer.

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

- append stable memories into `self_memory.jsonl`, `relation_memory.jsonl`, and `insight_memory.jsonl`
- rewrite `SOUL.md` when a stable self-pattern is confirmed
- rewrite `USER.md` when a stable user-pattern is confirmed

`SOUL.md` and `USER.md` updates are validated before write, and writes stay atomic.

#### HeartbeatService
Two-phase background task checker:

1. **Phase 1 (decide)**: LLM reads `HEARTBEAT.md` and calls `heartbeat({action: "skip"|"run"})`.
2. **Phase 2 (execute)**: Only if `run`, invokes the registered `on_execute` callback.

---

### Tools

Built-in tools available to the `central`:

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

`brain` and `central` can each use a **different model** through `agents.defaults.brainMode` and `agents.defaults.centralMode`, enabling cost/quality trade-offs.

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
│   ├── companion_brain.py
│   ├── decision_packet.py
│   └── event_narrator.py
├── execution/
│   ├── backend.py
│   ├── central_executor.py
│   ├── executor_context.py
│   ├── skills.py
│   ├── stream.py
│   └── tool_runtime.py
├── protocol/            # Typed runtime submissions / events / task results
├── runtime/
│   ├── event_bus.py
│   ├── event_loop.py
│   ├── input_gate.py
│   ├── manager.py
│   ├── running_task.py
│   ├── session_runtime.py
│   └── task_state.py
├── session/
│   ├── history_store.py
│   └── thread_store.py
├── memory/
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

