# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

**emoticorebot** is an ultra-lightweight personal AI assistant with an **EQ-led fusion architecture (EQ + IQ Layer)**, built on [LangGraph](https://github.com/langchain-ai/langgraph) and derived from the original Nanobot project.

It perceives emotional context in every conversation turn, lets **EQ act as the lead layer**, routes work into a lightweight **sparse MoE IQ layer** when needed, and continuously evolves its persona through background reflection.

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

emoticorebot uses a **LangGraph state machine** to execute each conversation turn. The external graph remains simple, but the internal semantics are now **EQ-led deliberation + lightweight sparse MoE**:

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
| `EQ Node` | Lead layer. Interprets intent, sets emotional goal, decides whether to consult IQ, chooses experts, reviews expert disagreement, and produces the final user-facing reply. |
| `IQ Layer` | Lightweight sparse MoE. Runs `ActionExpert` by default, and conditionally adds `MemoryOverlay` and `RiskOverlay`. |
| `Memory Node` | Writes event / episodic / semantic / relational / affective / plan memories and persists PAD state. |

**Routing logic (`FusionRouter`):**

- `EQ → IQ`: EQ decides the current turn needs internal rational work.
- `IQ → EQ`: IQ returns a merged packet plus expert packets / disagreement summary.
- `EQ → Memory`: EQ has decided to answer the user or ask the user for missing info.
- `* → Memory`: `done=True`; write-back and exit.

### Lightweight Sparse MoE in IQ Layer

The current IQ layer is intentionally lightweight instead of a heavy all-experts MoE.

| Expert | Default | Role |
|---|---|---|
| `ActionExpert` | Yes | Main expert for factual analysis, tools, missing params, and next-step recommendation |
| `MemoryOverlay` | Conditional | Injects historical task / plan / episodic context when the turn looks like a resume / follow-up |
| `RiskOverlay` | Conditional | Adds cheap risk checks when confidence is low, tools are used, or the action is sensitive |

Selection is **EQ-led**:

- default to `ActionExpert`
- add `MemoryOverlay` only for history continuation / pending-task recovery
- add `RiskOverlay` only for uncertainty, sensitive actions, or lower confidence
- cap the active set to **at most 2 experts** for latency and token control

Each IQ round returns both a **merged packet** and the underlying **expert packets**, so EQ can see internal disagreement instead of only a flattened result.

### Typical Workflows

#### 1. Normal request → `ActionExpert`

Example: “Help me summarize this file.”

```text
User
  → EQ judges this is a normal task
  → EQ selects: [ActionExpert]
  → IQ Layer runs ActionExpert
  → EQ finalizes user-facing reply
```

Properties:

- lowest cost path
- no extra memory lookup
- no extra risk overlay unless confidence drops

#### 2. Resume / follow-up request → `ActionExpert + MemoryOverlay`

Example:

- previous turn: “Check the weather for me.”
- assistant: “Which city?”
- user: “Shanghai.”

```text
User follow-up
  → EQ detects likely pending-task recovery
  → EQ selects: [ActionExpert, MemoryOverlay]
  → MemoryOverlay checks pending task / plans / episodic memory
  → ActionExpert continues the task with overlay context
  → EQ reviews merged result and answers naturally
```

Properties:

- optimized for unfinished-task recovery
- preserves `resume_task` and overlay hit type in session metadata
- avoids asking the same missing question again when enough context exists

#### 3. Sensitive / low-confidence request → `ActionExpert + RiskOverlay`

Example: “Run this command and delete the old files.”

```text
User request
  → EQ detects possible external action / higher risk
  → EQ selects: [ActionExpert, RiskOverlay]
  → ActionExpert evaluates feasibility and tools
  → RiskOverlay points out dangers / uncertainty / missing safeguards
  → EQ sees disagreement, then either answers conservatively or asks user first
```

Properties:

- optimized for safety and overconfidence control
- useful when tools are involved or confidence is low
- keeps the system lightweight by adding only one overlay

---

### Decision Inputs & Prompt Construction

The current implementation no longer uses standalone `SignalExtractor` / `PolicyEngine` modules. Instead, turn planning is produced by **`EQService` + `FusionRouter` + session metadata**.

**EQ prompt construction (`ContextBuilder.build_eq_system_prompt`)**

- loads EQ execution rules from workspace `AGENTS.md`
- loads persona anchors from `SOUL.md` and user cognition from `USER.md`
- loads `current_state.md` for PAD / status grounding
- retrieves relational / affective / reflective / episodic memory sections
- asks EQ to decide whether IQ is needed, which experts to activate, and what each expert should focus on

**IQ prompt construction (`ContextBuilder.build_iq_system_prompt`)**

- loads workspace `AGENTS.md` and `TOOLS.md` as execution constraints
- loads `current_state.md`
- retrieves semantic / episodic / plan / reflective / event memory sections
- injects active skills summary and skill bodies when configured
- passes EQ-selected experts and expert-specific questions through `intent_params`

**Session and continuation inputs**

- pending task metadata is injected before EQ deliberation and follow-up IQ rounds
- assistant-side metadata persists selected experts, expert packets, disagreement summary, and memory overlay anchors
- EQ arbitration now persists accepted experts, rejected experts, and a short arbitration summary
- `MemoryOverlay` can recover `resume_task` and hit type so EQ avoids re-asking the same missing question when enough context exists

In short, the working decision loop is now: **history + memory + pending task → EQ planning → sparse expert execution in IQ → EQ finalization**.

---

### Memory Layer

All memory is stored as files under `~/.emoticorebot/data/memory/` (or the configured workspace):

| Store | File | Purpose |
|-------|------|---------|
| `EventStore` | `events.jsonl` | Raw event stream for each turn |
| `EpisodicStore` | `episodic.jsonl` | Conversation episodes distilled from event slices |
| `SemanticStore` | `semantic.jsonl` | Durable facts with tags and importance scores |
| `ReflectiveStore` | `reflective.jsonl` | Higher-level insights derived from reflection cycles |
| `PlanStore` | `plans.jsonl` | Active / blocked / completed task memories |
| `RelationalStore` | `relational.jsonl` | Preferences, relationships, warm memories |
| `AffectiveStore` | `affective.jsonl` | PAD (Pleasure / Arousal / Dominance) emotional timeline |
| `MemoryFacade` | — | Unified read/write API for all stores |

The primary memory flow is now **event stream → episodic / semantic / reflective / plans**, rather than `MEMORY.md` / `HISTORY.md` file summaries.

In addition, assistant-side session metadata now persists:

- selected experts for the round
- expert disagreement summary
- compact expert summaries
- EQ arbitration result (`accepted_experts` / `rejected_experts` / `arbitration_summary`)
- `MemoryOverlay` hit type / `resume_task` / overlay summary

This makes later turns much better at resuming unfinished work and preserving the shape of past internal deliberation.

EQ arbitration is also written into structured long-term memory:

- assistant dialogue events now carry arbitration metadata for traceability
- `ReflectiveStore` now records `eq_arbitration` insights when a turn contains real expert selection, rejection, or multiple internal rounds
- later retrieval can reuse not just “what happened”, but also “how EQ chose between experts”

The **PAD model** (Pleasure-Arousal-Dominance) is used to track the bot's continuous emotional state across sessions. It is loaded at startup from `current_state.md` and written back after every turn.

---

### Current Limitations

The current architecture is already usable, but it is intentionally still conservative in a few places:

- `MemoryOverlay` is currently **rule-first** rather than a fully semantic planner; this keeps it cheap and fast, but it may miss more implicit follow-up turns.
- `RiskOverlay` is currently a **cheap heuristic overlay**, not yet a small-model specialist; it is good at lightweight guarding, but not yet a deep adversarial critic.
- IQ-layer merging is still **ActionExpert-centered**; overlay experts mainly refine or constrain the primary result rather than participate in a more advanced arbitration pipeline.
- EQ arbitration is now persisted, but reflective memory currently stores only a compact arbitration insight rather than a richer multi-step debate trace.
- The outer LangGraph remains intentionally simple; the system behaves like an EQ-led sparse MoE, but the graph itself is not yet a dedicated multi-expert state machine.

### Roadmap

Recommended next steps for this architecture:

1. **Upgrade `RiskOverlay` to a small-model expert**
   - make risk review more precise
   - keep the main path fast by only enabling it when needed

2. **Strengthen `MemoryOverlay` recovery quality**
   - improve implicit follow-up detection
   - better reconcile pending-task / plan / episodic signals

3. **Deepen EQ arbitration memory**
   - enrich `eq_arbitration` reflections with stronger causal tags
   - let future turns retrieve not only the verdict but also the failure mode that triggered it
   - prepare for richer agent-memory behavior later

4. **Refactor the IQ layer internally**
   - split planning / execution / merging more clearly
   - preserve the current lightweight behavior while making extension easier

5. **Optionally add more experts later**
   - only after `ActionExpert`, `MemoryOverlay`, and `RiskOverlay` are stable
   - examples: fact specialist, social-memory specialist, deeper planning specialist

In short: the current version optimizes for **clarity, controllable cost, and recoverable history**, and future work should improve expert quality without giving up the lightweight design.

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
├── core/                 # Fusion orchestration (LangGraph graph, state, router, context)
│   ├── graph.py          #   LangGraph graph definition & compilation
│   ├── state.py          #   FusionState / IQState / EQState
│   ├── router.py         #   FusionRouter (EQ ↔ IQ ↔ Memory routing)
│   ├── context.py        #   EQ / IQ prompt context builder
│   ├── model.py          #   LLMFactory (multi-provider)
│   ├── mcp.py            #   MCP client integration
│   ├── skills.py         #   Skill loader
│   └── nodes/            #   eq_node / iq_node / memory_node
├── experts/              # Lightweight sparse MoE experts for the IQ layer
│   ├── base.py
│   ├── registry.py
│   ├── action_expert.py
│   ├── memory_overlay.py
│   └── risk_overlay.py
├── services/             # Service layer
│   ├── eq_service.py     #   EQ lead service (deliberate / finalize / expert planning)
│   ├── iq_service.py     #   IQ Layer coordinator (sparse MoE + tool reasoning)
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
