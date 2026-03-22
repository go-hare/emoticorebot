# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` is a companion AI runtime built around a simple pipeline:

`Front -> Runtime -> Core -> Sleep`

- `Front`
  - lightweight, user-facing, streaming-first
- `Runtime`
  - pure scheduler, no LLM decision-making
- `Core`
  - the only main brain, powered by OpenAI Agents SDK
- `Sleep`
  - async reflection and crystallization
- `Memory / World Model / Skills`
  - app-owned stores, not framework-owned black boxes

Current architecture doc:

- [docs/openai-agents-architecture.zh-CN.md](docs/openai-agents-architecture.zh-CN.md)

---

## Install

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

---

## Quick Start

1. Initialize workspace:

```bash
emoticorebot onboard
```

2. Configure `~/.emoticorebot/config.json`:

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

3. Start CLI chat:

```bash
emoticorebot agent
```

---

## Workspace Layout

After onboarding, the workspace keeps these key files and folders:

```text
~/.emoticorebot/workspace/
  USER.md
  SOUL.md
  AGENTS.md
  TOOLS.md
  HEARTBEAT.md
  current_state.md
  memory/
    cognitive_events.jsonl
    memory.jsonl
    vector/
  session/
    <thread_id>/
      brain.jsonl
      tool.jsonl
  state/
    world_model.json
  skills/
  templates/
    FRONT.md
    FRONT_FOLLOWUP.md
    CORE_MAIN.md
    SLEEP.md
```

---

## Project Layout

```text
emoticorebot/
  app/
  channels/
  cli/
  config/
  core/
  front/
  providers/
  runtime/
  sleep/
  state/
  templates/
  tools/
  skills/
```
