# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` 是一个面向桌面陪伴式 Agent 的主干项目。它不是把聊天、任务、情绪和记忆拆成几套彼此割裂的系统，而是试图把“先回应、再执行、持续陪伴”收进同一个核心里。项目的目标不是做一个臃肿的平台，而是提供一个足够清楚、足够稳定、能继续长出来的 Agent 主干，让它既能像桌面伙伴一样自然互动，也能像后台主脑一样持续推进任务。

在这套设计里，用户不会每次都先撞上一个缓慢的后台执行器。前台层会先接住用户，结合情绪状态、PAD、陪伴风格和桌面表达给出自然回应；后台常驻内核再继续处理真正的任务理解、工具调用、多任务 run、记忆沉淀和 sleep。中间只保留一层很薄的 Runtime 负责桥接，把整套系统收在一条简单但完整的主线上。

它强调的不是“再套很多层执行架构”，而是把最重要的几件事收进一条清楚的主线里：

`Front -> Runtime -> BrainKernel`

一句话说，它想做的是：

**先回应你，持续陪着你，再由后台主脑把事情真正做完。**

---

## 特点

- 前台优先
  用户输入先到 `Front`，可以先得到一句自然回应，而不是每次都卡在后台推理上。
- 情绪驱动
  项目内置 affect runtime，维护 PAD、活力值、压力值等状态，让表达不是死模板。
- 桌面陪伴表达
  除了文本回复，系统还会输出 companion / surface 状态，用来驱动桌面体的在场感、动作感和气质变化。
- 常驻单脑
  `BrainKernel` 在进程里常驻运行，任务、工具、记忆、sleep 都归它负责。
- 原生多任务
  多任务不是外挂调度层，而是内核内部的 run 模型，支持 foreground / background 任务状态。
- 记忆与睡眠内建
  前台事件、脑内记录、长期记忆沉淀和 sleep agent 都已经在主干里。
- 多前端复用
  CLI、桌面端，以及后续的语音、视频、机器人端，都可以复用同一条输出链路。

---

## 核心结构

- `Front`
  前台表达层，也可以理解成“嘴”。负责即时回应、陪伴语气和结果润色。
- `Runtime`
  桥接层。负责接收输入、连接前台与内核、统一输出分发。
- `BrainKernel`
  唯一主脑。负责任务理解、工具调用、多任务、记忆和 sleep。

这套分工很明确：

- `Front` 负责怎么说
- `BrainKernel` 负责怎么做
- `Runtime` 负责怎么接

---

## 适合什么

`emoticorebot` 更适合下面这类项目：

- 桌面陪伴机器人
- 需要“先回应，再执行”的 Agent
- 希望主脑常驻运行，而不是每轮临时函数调用的系统
- 希望把多任务、记忆、sleep 收进同一个核心里的项目

它不强调“大而全”，而强调：

- 主干清楚
- 交互自然
- 方便继续演进

---

## 快速开始

### 安装

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

如果要启动桌面壳，还需要：

- Node.js / npm
- Rust / cargo

### 初始化工作区

```bash
python -m emoticorebot onboard
```

### 配置模型

编辑 `~/.emoticorebot/config.json`。

DeepSeek 示例：

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

说明：

- `brainMode` 当前对应 `Front`
- `executorMode` 当前对应 `BrainKernel`

### 启动 CLI

```bash
python -m emoticorebot agent
```

### 启动桌面桥

```bash
python -m emoticorebot desktop
```

### 启动桌面开发模式

```bash
python -m emoticorebot desktop-dev
```

Windows 也可以直接：

```bash
start_desktop.cmd
```

---

## 项目结构

```text
emoticorebot/
  desktop-shell/      Tauri + Vite 桌面壳
  emoticorebot/
    app/              应用装配
    brain_kernel/     常驻内核
    cli/              CLI 和桌面启动入口
    companion/        陪伴表达和 surface orchestration
    config/           配置
    desktop/          桌面桥接
    front/            前台表达层
    runtime/          运行时桥接
    affect/           情绪 / PAD / 活力 / 压力
    tools/            工具
  tests/
  start_desktop.cmd
```

如果你要从代码入口开始看，建议从这里开始：

- `emoticorebot/app/factory.py`
- `emoticorebot/runtime/scheduler.py`
- `emoticorebot/front/service.py`
- `emoticorebot/brain_kernel/agent.py`
- `emoticorebot/brain_kernel/resident.py`
- `emoticorebot/brain_kernel/routing.py`
- `emoticorebot/brain_kernel/sleep_agent.py`

---

## 当前边界

- 同一会话内部仍然是单队列串行
- 一个很长的 LLM 回合会阻塞该会话后续事件
- `brainMode / executorMode` 仍是历史字段名
- 桌面壳现在更偏开发态集成

这些是当前版本的边界，不是隐藏行为。

---

## 测试

```bash
pytest -q
```

主干链路测试：

```bash
pytest -q tests/test_runtime_scheduler.py tests/test_desktop_server.py tests/test_desktop_adapter.py tests/test_front_service.py tests/test_brain_kernel.py
```
