# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` 当前采用一条极简主线：

`Front -> Runtime -> BrainKernel`

- `Front`
  - 只负责前台表达，使用 LangChain
- `Runtime`
  - 只负责桥接，收消息、投递常驻内核、等待输出
- `BrainKernel`
  - 唯一主脑，工具循环、记忆、sleep 都在内核里自己完成

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

## 项目结构

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
