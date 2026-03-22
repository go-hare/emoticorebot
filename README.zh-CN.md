# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` 当前采用一条极简主线：

`Front -> Runtime -> Core -> Sleep`

- `Front`
  - 只负责前台表达，优先流式和轻量
- `Runtime`
  - 只负责调度，不负责 LLM 决策
- `Core`
  - 唯一主脑，直接做工具循环，使用 OpenAI Agents SDK
- `Sleep`
  - 异步反思、沉淀、结晶
- `Memory / World Model / Skills`
  - 由应用自己掌控

当前架构文档：

- [docs/openai-agents-architecture.zh-CN.md](docs/openai-agents-architecture.zh-CN.md)

---

## 安装

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

---

## 快速开始

1. 初始化工作区：

```bash
emoticorebot onboard
```

2. 编辑 `~/.emoticorebot/config.json`：

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

3. 启动 CLI：

```bash
emoticorebot agent
```

---

## 工作区结构

初始化后，工作区会保留这些关键文件：

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

## 项目结构

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
