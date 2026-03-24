# emoticorebot

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` is a lightweight agent core for desktop companion scenarios.

The current project keeps one simple backbone:

`Front -> Runtime -> BrainKernel`

- `Front`
  the user-facing layer, or the "mouth"; it responds first and rewrites backend output into natural text
- `Runtime`
  the bridge; it receives input, forwards turns to the front layer and the resident kernel, then fan-outs output
- `BrainKernel`
  the only real brain; tasks, multitask runs, tools, memory, and sleep all live inside the kernel

---

## What This Project Is

This is not trying to be a giant platform. It is a working agent backbone with:

- immediate front replies
- a resident backend kernel
- run-level multitask support
- tool calling
- memory
- a sleep agent
- CLI and desktop shell integration

It is especially aimed at desktop companion use cases:

- `Front` owns tone, presence, and expression
- `BrainKernel` owns task understanding and execution
- `Runtime` does not make semantic decisions; it only bridges

---

## Why This Backbone

- Simple architecture
  There is no extra executor stack piled on top of the core path, which keeps evolution and replacement cheaper.
- Fast front-facing response
  `Front` can answer immediately instead of waiting for the backend kernel to finish every turn.
- Clear responsibility split
  Front handles expression, the kernel handles work, and Runtime only bridges.
- Easy client integration
  CLI, desktop, and future voice/video clients can all reuse the same output path.
- Multitask support stays inside the kernel
  Multitask behavior is owned by the run model itself, not by an external orchestration layer.
- Memory and sleep are built in
  Long-term consolidation is part of the backbone instead of a separate stitched-on service.

---

## How It Runs

A turn roughly looks like this:

1. user input enters `Runtime`
2. `Front` replies immediately
3. Runtime asynchronously publishes the turn into the resident `BrainKernel`
4. the kernel processes the turn
5. the result goes back through `Front`
6. final output is sent to desktop, CLI, and future clients through one unified output line

Text output is already unified. Different clients consume the same front output stream.

---

## Current Capabilities

- one real backend brain
- front-first interaction
- resident in-process kernel
- foreground/background runs inside one conversation
- memory and sleep consolidation
- desktop bridge and CLI entrypoints

---

## Current Boundaries

- a single conversation is still processed serially
- a long LLM turn blocks later events in that same conversation
- `brainMode / executorMode` are still legacy config names
- the desktop shell is still a dev integration, not a polished product shell

---

## Project Layout

```text
emoticorebot/
  desktop-shell/      Tauri + Vite desktop shell
  emoticorebot/
    app/              app assembly
    brain_kernel/     resident kernel
    cli/              CLI and desktop launchers
    config/           config schema and loader
    desktop/          desktop bridge
    front/            front expression layer
    providers/        model factory
    runtime/          runtime bridge
    tools/            tool implementations
  tests/
  start_desktop.cmd
```

Useful code entrypoints:

- `emoticorebot/app/factory.py`
- `emoticorebot/runtime/scheduler.py`
- `emoticorebot/front/service.py`
- `emoticorebot/brain_kernel/agent.py`
- `emoticorebot/brain_kernel/resident.py`
- `emoticorebot/brain_kernel/routing.py`
- `emoticorebot/brain_kernel/sleep_agent.py`

---

## Install

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

---

## Quick Start

### 1. Initialize the workspace

```bash
python -m emoticorebot onboard
```

### 2. Configure models

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

### 3. Start the CLI

```bash
python -m emoticorebot agent
```

### 4. Start the desktop bridge

```bash
python -m emoticorebot desktop
```

### 5. Start desktop dev mode

```bash
python -m emoticorebot desktop-dev
```

On Windows you can also use:

```bash
start_desktop.cmd
```

---

## Desktop Startup Notes

- `desktop-dev` requires `npm` and `cargo`
- if `start_desktop.cmd` exits with `9009`, the new `cmd.exe` environment usually cannot find `python` or `py`
- if `tauri dev` reports `failed to get cargo metadata: program not found`, the current environment cannot find `cargo`

---

## Tests

```bash
pytest -q
```

Core runtime checks:

```bash
pytest -q tests/test_runtime_scheduler.py tests/test_desktop_server.py tests/test_desktop_adapter.py tests/test_front_service.py tests/test_brain_kernel.py
```
