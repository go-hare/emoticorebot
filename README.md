# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` is a backbone project for desktop companion agents. It is not trying to split chat, task execution, emotion, and memory into disconnected subsystems. Instead, it tries to keep "respond first, execute next, stay present throughout" inside one coherent core. The goal is not to become a bloated platform, but to offer a clear and durable agent backbone that can feel natural as a desktop companion while still acting like a resident backend brain.

In this design, the user does not always hit a slow backend executor first. The front layer responds early, shaped by affect state, PAD, companion style, and desktop expression; the resident backend kernel then continues with actual task understanding, tool use, multitask runs, memory consolidation, and sleep. A very thin Runtime sits in the middle as the bridge, keeping the whole system on one simple but complete path.

Instead of stacking more orchestration layers, it keeps the core path simple and explicit:

`Front -> Runtime -> BrainKernel`

In one sentence:

**respond first, stay present, then let the backend brain actually finish the work.**

---

## Highlights

- Front-first interaction
  User input reaches `Front` first, so the system can answer naturally before the backend finishes a full turn.
- Affect-driven expression
  The project includes an affect runtime with PAD, vitality, and pressure state, so expression is not just fixed phrasing.
- Companion-style surface output
  Beyond text, the system emits companion and surface-state signals for desktop presence, motion, and expression.
- One resident brain
  `BrainKernel` stays alive in-process and owns tasks, tools, memory, and sleep in one place.
- Native multitask support
  Multitask behavior lives inside the kernel through a run model with foreground and background task state.
- Built-in memory and sleep
  Front events, brain traces, long-term consolidation, and a sleep agent are already part of the backbone.
- Reusable client path
  CLI, desktop, and future voice/video/robot clients can all reuse the same output path.

---

## Core Structure

- `Front`
  The user-facing layer, or the "mouth". It handles immediate replies, tone, and rewriting backend output.
- `Runtime`
  The bridge. It receives input, connects the front layer with the resident kernel, and fans output back out.
- `BrainKernel`
  The only real brain. It handles task understanding, tool use, multitask runs, memory, and sleep.

The split is intentional:

- `Front` decides how to say it
- `BrainKernel` decides how to do it
- `Runtime` decides how to wire it

---

## What It Fits

`emoticorebot` is a better fit for projects like:

- desktop companion agents
- systems that should answer before backend execution fully completes
- resident-kernel designs instead of pure function-call flows
- projects that want multitask, memory, and sleep inside one core

It is not trying to be a giant platform. The bias is toward:

- a clear backbone
- natural interaction
- easier long-term evolution

---

## Quick Start

### Install

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

If you want the desktop shell, you also need:

- Node.js / npm
- Rust / cargo

### Initialize the workspace

```bash
python -m emoticorebot onboard
```

### Configure models

Edit `~/.emoticorebot/config.json`.

DeepSeek example:

```json
{
  "providers": {
    "deepseek": {
      "apiKey": "sk-xxx",
      "apiBase": "https://api.deepseek.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "workspace": "~/.emoticorebot/workspace",
      "brainMode": {
        "model": "deepseek-chat",
        "provider": "deepseek"
      },
      "executorMode": {
        "model": "deepseek-chat",
        "provider": "deepseek"
      }
    }
  }
}
```

Notes:

- `brainMode` currently maps to `Front`
- `executorMode` currently maps to `BrainKernel`

### Start the CLI

```bash
python -m emoticorebot agent
```

### Start the desktop bridge

```bash
python -m emoticorebot desktop
```

### Start desktop dev mode

```bash
python -m emoticorebot desktop-dev
```

On Windows you can also use:

```bash
start_desktop.cmd
```

---

## Project Layout

```text
emoticorebot/
  desktop-shell/      Tauri + Vite desktop shell
  emoticorebot/
    app/              app assembly
    brain_kernel/     resident kernel
    cli/              CLI and desktop launchers
    companion/        companion expression and surface orchestration
    config/           configuration
    desktop/          desktop bridge
    front/            front expression layer
    runtime/          runtime bridge
    affect/           affect, PAD, vitality, pressure
    tools/            tool implementations
  tests/
  start_desktop.cmd
```

Good entrypoints for reading the code:

- `emoticorebot/app/factory.py`
- `emoticorebot/runtime/scheduler.py`
- `emoticorebot/front/service.py`
- `emoticorebot/brain_kernel/agent.py`
- `emoticorebot/brain_kernel/resident.py`
- `emoticorebot/brain_kernel/routing.py`
- `emoticorebot/brain_kernel/sleep_agent.py`

---

## Current Boundaries

- a single conversation is still processed serially
- one long LLM turn blocks later events in that same conversation
- `brainMode / executorMode` are still legacy config names
- the desktop shell is still more of a dev integration than a polished product shell

These are current design boundaries, not hidden behavior.

---

## Tests

```bash
pytest -q
```

Core runtime checks:

```bash
pytest -q tests/test_runtime_scheduler.py tests/test_desktop_server.py tests/test_desktop_adapter.py tests/test_front_service.py tests/test_brain_kernel.py
```
