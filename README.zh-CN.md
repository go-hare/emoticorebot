# emoticorebot（中文）

<p align="center">
  <img src="emoticorebot_logo.png" alt="emoticorebot logo" width="180"/>
</p>

`emoticorebot` 是一个面向桌面陪伴场景的 Agent 主干项目。

当前版本坚持一条很简单的主线：

`Front -> Runtime -> BrainKernel`

- `Front`
  负责前台表达，也就是“嘴”。先接住用户，再把后台结果润色成自然回复。
- `Runtime`
  负责桥接。接收输入、转发给前台和常驻内核、再把输出分发给桌面端或 CLI。
- `BrainKernel`
  负责真正干活。任务、多任务、工具、记忆、sleep 都在内核内部完成。

---

## 这个项目现在是什么

它不是一个大而全的平台，而是一个已经能跑起来的 Agent 主干：

- 前台可以即时回应
- 后台内核常驻运行
- 支持 run 级多任务
- 支持工具调用
- 支持记忆和 sleep agent
- 能接 CLI 和桌面壳

这套结构更适合“桌面陪伴机器人”这种方向：

- `Front` 负责说话方式和陪伴感
- `BrainKernel` 负责任务理解和执行
- `Runtime` 不做语义决策，只负责接线

---

## 这套主干的优势

- 架构简单
  没有额外叠很多执行层，主干清楚，后续替换和演进成本更低。
- 前台响应快
  `Front` 先回用户，不需要每次都等后台内核完整跑完。
- 职责清晰
  前台负责表达，内核负责干活，Runtime 只负责桥接，不互相抢职责。
- 易于接前端
  CLI、桌面端、后续语音或视频端，都可以复用同一条输出链路。
- 多任务能力内聚
  多任务不是外挂在外面的调度系统，而是内核内部的 run 模型。
- 记忆和睡眠是内建的
  不需要再额外拼一套记忆服务，主干本身就能沉淀长期信息。

---

## 当前项目的运行方式

一轮消息的大致流程是：

1. 用户输入先进入 `Runtime`
2. `Front` 先给出即时回复
3. Runtime 再把这一轮输入异步投给常驻 `BrainKernel`
4. 内核处理完成后，结果再回到 `Front`
5. 最终输出统一发给桌面端、CLI 和未来的其他前端

当前文本输出已经收敛成统一通道，不再按不同渠道各写一套回调逻辑。

---

## 当前能力

- 单一主脑：只有一个真正的后台脑 `BrainKernel`
- 前台先说：用户说话后，Front 先回应，不等内核完成
- 常驻内核：内核在当前进程里长期运行
- 多任务：一个会话里可以有多个 run，包含 foreground / background
- 记忆：包含前台事件、脑内记录和长期记忆沉淀
- 睡眠：每轮结束后可以由 sleep agent 做记忆整理

---

## 当前边界

- 同一会话内部仍然是单队列串行
- 一个很长的 LLM 回合会阻塞该会话后续事件
- `brainMode / executorMode` 还是历史字段名
- 桌面壳目前是开发态接入，不是最终产品形态

---

## 项目结构

```text
emoticorebot/
  desktop-shell/      Tauri + Vite 桌面壳
  emoticorebot/
    app/              应用装配
    brain_kernel/     常驻内核
    cli/              CLI 和桌面启动入口
    config/           配置
    desktop/          桌面桥接
    front/            前台表达层
    providers/        模型工厂
    runtime/          运行时桥接
    tools/            工具
  tests/
  start_desktop.cmd
```

如果你要快速读代码，建议从这里开始：

- `emoticorebot/app/factory.py`
- `emoticorebot/runtime/scheduler.py`
- `emoticorebot/front/service.py`
- `emoticorebot/brain_kernel/agent.py`
- `emoticorebot/brain_kernel/resident.py`
- `emoticorebot/brain_kernel/routing.py`
- `emoticorebot/brain_kernel/sleep_agent.py`

---

## 安装

```bash
git clone https://github.com/go-hare/emoticorebot.git
cd emoticorebot
pip install -e .
```

---

## 快速开始

### 1. 初始化工作区

```bash
python -m emoticorebot onboard
```

### 2. 配置模型

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

### 3. 启动 CLI

```bash
python -m emoticorebot agent
```

### 4. 启动桌面桥

```bash
python -m emoticorebot desktop
```

### 5. 启动桌面开发模式

```bash
python -m emoticorebot desktop-dev
```

Windows 也可以直接：

```bash
start_desktop.cmd
```

---

## 桌面启动注意事项

- `desktop-dev` 需要 `npm` 和 `cargo`
- 如果双击 `start_desktop.cmd` 出现 `exit code 9009`，通常是当前 `cmd` 环境里找不到 `python` 或 `py`
- 如果 `tauri dev` 报 `failed to get cargo metadata: program not found`，说明当前环境找不到 `cargo`

---

## 测试

```bash
pytest -q
```

主干链路测试：

```bash
pytest -q tests/test_runtime_scheduler.py tests/test_desktop_server.py tests/test_desktop_adapter.py tests/test_front_service.py tests/test_brain_kernel.py
```
