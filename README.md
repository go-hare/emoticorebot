# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` is a companion AI runtime built around a simple pipeline:

`Front -> Runtime -> BrainKernel`

- `Front`
  - lightweight, user-facing, built with LangChain
- `Runtime`
  - pure bridge: receives messages, forwards them to the resident kernel, waits for output
- `BrainKernel`
  - the only main brain, tool loop + memory + sleep all owned inside the kernel

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
  memory/
    cognitive_events.jsonl
    memory.jsonl
  session/
    <thread_id>/
      brain.jsonl
      front.jsonl
      tool.jsonl
  skills/
  templates/
    FRONT.md
```

---

## Project Layout

```text
emoticorebot/
  app/
  brain_kernel/
  channels/
  cli/
  config/
  front/
  providers/
  runtime/
  templates/
  tools/
  skills/
```
