# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is an ultra-lightweight personal AI assistant with an **EQ-led fusion architecture (EQ + IQ Layer)**, built on [LangGraph](https://github.com/langchain-ai/langgraph) and derived from the original Nanobot project.

It perceives emotional context in every conversation turn, lets **EQ act as the lead layer**, routes complex work into a **Deep Agents-based IQ layer** when needed, and continuously evolves its persona through background reflection.

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

### EQ-led Fusion Graph (LangGraph)

emoticorebot uses a **LangGraph state machine** to execute each conversation turn. The external graph remains simple, but the internal semantics are now **EQ-led deliberation + Deep Agents execution**:

```
User Input
    │
    ▼
Session history / pending-task metadata
    │
    ▼
 ┌──────────────────────────────────────────────────────┐
 │                  LangGraph Graph                     │
 │                                                      │
 │   ENTRY ──→ [EQ Node] ──┬──→ [IQ Layer] ──┐         │
 │               ▲         │        │         │         │
 │               └─────────┘        ▼         │         │
 │                          [Memory Node] ←───┘         │
 │                                │                     │
 └────────────────────────────────┼─────────────────────┘
                                  ▼
                               END / Output
```

`EQ Node` and `IQ Layer` may loop for multiple internal rounds, but **only EQ can terminate the turn and produce the user-facing message**.

**Node responsibilities:**

| Node | Role |
|------|------|
| `EQ Node` | Lead layer. Interprets intent, decides whether to consult IQ, tracks task continuity, and produces the final user-facing reply. |
| `IQ Layer` | Deep Agents-based execution layer. Handles planning, tool use, subagents, and long-running complex tasks. |
| `Memory Node` | Writes the event stream and persists PAD state. |

**Routing logic (`FusionRouter`):**

- `EQ → IQ`: EQ decides the current turn needs internal rational work.
- `IQ → EQ`: IQ returns task analysis, evidence, risks, missing params, and a recommended next action.
- `EQ → Memory`: EQ has decided to answer the user or ask the user for missing info.
- `* → Memory`: `done=True`; write-back and exit.

### Deep Agents in IQ Layer

The current IQ layer no longer uses the older expert-overlay pipeline. Instead, it is powered by Deep Agents while preserving the same compact EQ↔IQ contract.

| Component | Role |
|---|---|---|
| Planner | Breaks down the internal task and decides how to proceed |
| Tools | Executes registered capabilities such as web search, fetch, messaging, and cron |
| Subagents | Offloads focused work such as research or workspace-side operations |
| Skills | Reuses local workflow instructions from the workspace `skills/` directory |

The contract remains **EQ-led**:

- EQ decides whether IQ is needed and what internal task should be delegated
- IQ plans and executes using tools, subagents, and skills
- IQ returns a normalized packet with `status`, `analysis`, `evidence`, `risks`, `missing`, and `recommended_action`
- EQ decides whether to answer, ask the user, or continue internal deliberation

This keeps the outer loop stable while allowing the inner IQ engine to become more capable over time.

### Typical Workflows

#### 1. Normal request → direct IQ execution

Example: “Help me summarize this file.”

```text
User
  → EQ judges this is a normal task
  → EQ delegates a concrete internal task to IQ
  → IQ plans and executes it
  → EQ finalizes user-facing reply
```

Properties:

- simple handoff from EQ to IQ
- no raw tool output is exposed directly to the user
- EQ still controls the final wording

#### 2. Resume / follow-up request → EQ continuity + IQ execution

Example:

- previous turn: “Check the weather for me.”
- assistant: “Which city?”
- user: “Shanghai.”

```text
User follow-up
  → EQ detects likely pending-task recovery
  → EQ reconstructs continuity from session and memory
  → IQ continues the delegated task with the recovered context
  → EQ reviews merged result and answers naturally
```

Properties:

- optimized for unfinished-task recovery
- keeps cross-turn continuity on the EQ side
- avoids asking the same missing question again when enough context exists

#### 3. Sensitive / low-confidence request → IQ analysis + EQ safeguard

Example: “Run this command and delete the old files.”

```text
User request
  → EQ detects possible external action / higher risk
  → EQ delegates a cautious internal task
  → IQ evaluates feasibility, evidence, and missing safeguards
  → EQ either answers conservatively, asks the user first, or continues internal deliberation
```

Properties:

- optimized for safety and overconfidence control
- useful when tools are involved or confidence is low
- keeps the user-facing voice unified through EQ

---

### Decision Inputs & Prompt Construction

The current implementation no longer uses standalone `SignalExtractor` / `PolicyEngine` modules. Instead, turn planning is produced by **`EQService` + `FusionRouter` + session metadata**.

**EQ prompt construction (`ContextBuilder.build_eq_system_prompt`)**

- loads EQ execution rules from workspace `AGENTS.md`
- loads persona anchors from `SOUL.md` and user cognition from `USER.md`
- loads `current_state.md` for PAD / status grounding
- retrieves EQ event stream sections
- asks EQ to decide whether IQ is needed and what internal task should be delegated

**IQ prompt construction (`ContextBuilder.build_iq_system_prompt`)**

- loads workspace `AGENTS.md` and `TOOLS.md` as execution constraints
- loads `current_state.md`
- retrieves event stream sections
- injects active skills summary and skill bodies when configured
- passes pending-task and missing-parameter hints through `intent_params`

**Session and continuation inputs**

- pending task metadata is injected before EQ deliberation and follow-up IQ rounds
- assistant-side metadata persists task continuity, task labels, and compact IQ summaries
- lightweight IQ state such as `iq_status`, `iq_confidence`, and `iq_missing_params` is preserved for continuation
- `resume_task` recovery helps EQ avoid re-asking the same missing question when enough context exists

In short, the working decision loop is now: **history + memory + pending task → EQ planning → IQ execution → EQ finalization**.

---

### Memory Layer

All memory is stored as files under `~/.emoticorebot/data/memory/` (or the configured workspace):

| Store | File | Purpose |
|-------|------|---------|
| `EventStore` | `events.jsonl` | Raw event stream for each turn |
| `EventStore` | `events.jsonl` | Raw conversation event stream with EQ judgments |
| `MemoryFacade` | — | Unified read/write API for all stores |

The primary memory flow is now simply **event stream**, with higher-level memory intended to be derived later rather than persisted as parallel stores.

In the current implementation, the IQ execution layer only receives the **current internal task delegated by EQ**. It no longer replays user/assistant conversation history; cross-turn continuity stays on the EQ side.

In addition, assistant-side session history now persists a lightweight record:

- compact `iq_summary` for future turn recovery
- `iq_status`, `iq_confidence`, and `iq_missing_params` for lightweight continuation
- task continuity metadata such as `task_label` and pending-task state

The hot `sessions/*.jsonl` path stays intentionally small, so later turns can recover context without replaying every internal IQ detail.

This makes later turns much better at resuming unfinished work and preserving the shape of past internal deliberation.

Internal multi-round IQ behavior is also written into structured long-term memory:

- assistant dialogue events now carry the final EQ decision for traceability
- Higher-level reflections are intended to be derived from the event stream rather than persisted as a parallel store.
- later retrieval can reuse not just “what happened”, but also “how the internal process unfolded”

The **PAD model** (Pleasure-Arousal-Dominance) is used to track the bot's continuous emotional state across sessions. It is loaded at startup from `current_state.md` and written back after every turn.

---

### Current Limitations

The current architecture is already usable, but it is intentionally still conservative in a few places:

- Deep Agents output still needs normalization into the compact EQ↔IQ packet, so richer intermediate traces are not yet fully preserved.
- The current tool set is intentionally narrow; broader workspace and research coverage can still be added.
- Cross-turn continuity is still EQ-centric and conservative; implicit follow-up recovery can become stronger.
- Reflective memory stores compact process summaries rather than full internal execution traces.
- The outer LangGraph remains intentionally simple; most sophistication still lives inside the IQ engine.

### Roadmap

Recommended next steps for this architecture:

1. **Improve Deep Agents observability**
   - preserve richer execution traces without bloating session history
   - expose better debugging hooks for internal planning and subagents

2. **Strengthen continuity recovery**
   - improve implicit follow-up detection
   - better reconcile pending-task / plan / memory signals

3. **Deepen process memory**
   - enrich `iq_process` reflections with stronger causal tags
   - let future turns retrieve not only the outcome but also the failure mode that triggered it
   - prepare for richer agent-memory behavior later

4. **Refactor the IQ layer internally**
   - split planning / execution / merging more clearly
   - preserve the current lightweight behavior while making extension easier

5. **Expand tools, skills, and subagents carefully**
   - only after the current Deep Agents workflow is stable
   - examples: stronger research workers, richer workspace helpers, domain-specific skills

In short: the current version optimizes for **clarity, controllable cost, and recoverable history**, and future work should improve IQ execution quality without giving up the lightweight outer design.

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
Reads recent events and calls an LLM to produce structured JSON:

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
├── core/                 # Fusion orchestration (LangGraph graph, state, router, context)
│   ├── graph.py          #   LangGraph graph definition & compilation
│   ├── state.py          #   FusionState / IQState / EQState
│   ├── router.py         #   FusionRouter (EQ ↔ IQ ↔ Memory routing)
│   ├── context.py        #   EQ / IQ prompt context builder
│   ├── model.py          #   LLMFactory (multi-provider)
│   ├── mcp.py            #   MCP client integration
│   ├── skills.py         #   Skill loader
│   └── nodes/            #   eq_node / iq_node / memory_node
├── services/             # Service layer
│   ├── eq_service.py     #   EQ lead service (deliberate / finalize)
│   ├── iq_service.py     #   IQ execution layer (Deep Agents + subagents)
│   ├── memory_service.py #   Memory read/write service
│   └── tool_manager.py   #   Tool registry & execution
├── memory/               # Layered memory implementation
│   ├── structured_stores.py
│   ├── stateful_stores.py
│   ├── extractor.py
│   ├── retriever.py
│   ├── schema.py
│   ├── jsonl_store.py
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
├── bus/                  # Inbound / outbound event queue
├── cron/                 # Cron scheduler service
├── session/              # Session persistence and recovery
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
