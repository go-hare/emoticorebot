# 单大脑 + 执行层重构蓝图

这份文档不是过渡方案，而是目标态蓝图。

这次重构采用两个明确原则：

- `breaking`
  - 不做兼容层
  - 不保留旧命名 API
  - 不再自动读取旧结构
- `按模块推进`
  - 一次只重构一层
  - 每层改完后，立即统一命名、测试和文档

目标是把当前 `left_brain / right_brain` 叙事，整体收敛为：

- `Brain`
  - 唯一主体
  - 唯一和用户对话
  - 决定回复、任务主线、是否触发浅反思、下一条执行动作
- `Executor`
  - 后台异步执行层
  - 只消费 `check`
  - 不直接和用户说话
  - 只回写事实与产物
- `World Model`
  - 共享运行态
  - 记录当前主题、当前任务、主线、当前阶段、当前 checks、最近结果

---

## 1. 重构目标

### 1.1 语义目标

- 去掉“双脑并列主体”的概念混乱
- 保留“一个主体 + 一个执行层”的体验
- 让用户可见输出始终只有大脑负责
- 让执行层只负责执行，不主动发言
- 让世界模型成为共享运行态，而不是上下文杂物箱

### 1.2 工程目标

- 模块命名从 `left/right` 收敛到 `brain/executor`
- 会话落盘文件从 `left.jsonl / right.jsonl` 收敛到 `brain.jsonl / executor.jsonl`
- 反思、记忆、上下文构建与世界模型边界清晰
- 为后续 `Brain = ratAgent`、`Executor = deepAgent` 留出稳定接口

---

## 2. 目标目录结构

建议收敛到下面这套目录，不再让“架构概念”和“历史命名”混在一起：

```text
emoticorebot/
  brain/
    runtime.py
    packet.py
    prompt.py
    policy.py
  executor/
    runtime.py
    agent.py
    backend.py
    store.py
  world_model/
    schema.py
    store.py
    reducers.py
    projectors.py
  context/
    builder.py
    memory_context.py
    workspace_context.py
  reflection/
    turn.py
    deep.py
    governor.py
    crystallizer.py
  runtime/
    kernel.py
    transport_bus.py
    event_bus.py
  session/
    runtime.py
    thread_store.py
    models.py
  tools/
    manager.py
    registry.py
  memory/
    cognitive_events.jsonl
    long_term/
      memory.jsonl
    vector/
```

说明：

- `brain/`
  - 只放大脑决策和输出协议
- `executor/`
  - 只放执行层运行时、审核钩子、工具编排
- `world_model/`
  - 独立成层，不再夹在 session 或 memory 里
- `context/`
  - 放 `USER.md`、`SOUL.md`、记忆检索、工作区信息等上下文拼装
- `reflection/`
  - 继续保留，但明确由大脑触发浅反思，由浅反思决定是否进入深反思

---

## 3. 模块迁移映射

### 3.1 代码目录映射

| 当前路径 | 目标路径 | 说明 |
|------|------|------|
| `emoticorebot/left_brain/runtime.py` | `emoticorebot/brain/runtime.py` | 大脑主运行时 |
| `emoticorebot/left_brain/packet.py` | `emoticorebot/brain/packet.py` | 大脑输出协议解析 |
| `emoticorebot/left_brain/context.py` | `emoticorebot/context/builder.py` | 上下文构建独立出脑模块 |
| `emoticorebot/right_brain/runtime.py` | `emoticorebot/executor/runtime.py` | 执行层运行时 |
| `emoticorebot/right_brain/executor.py` | `emoticorebot/executor/agent.py` | 执行代理主体 |
| `emoticorebot/right_brain/backend.py` | `emoticorebot/executor/backend.py` | 执行层审核钩子与后端约束 |
| `emoticorebot/right_brain/store.py` | `emoticorebot/executor/store.py` | 执行态存储 |
| `emoticorebot/session/thread_store.py` | `emoticorebot/session/thread_store.py` | 保留路径，但内部命名改成 `brain/executor` |
| `emoticorebot/runtime/kernel.py` | `emoticorebot/runtime/kernel.py` | 保留路径，语义从 left/right runtime graph 收敛到 brain/executor graph |

### 3.2 类名映射

| 当前类名 | 目标类名 |
|------|------|
| `LeftBrainRuntime` | `BrainRuntime` |
| `RightBrainRuntime` | `ExecutorRuntime` |
| `RightBrainExecutor` | `ExecutorAgent` |
| `LeftDecisionPacket` | `BrainDecisionPacket` |

---

## 4. 会话与运行态文件

### 4.1 会话历史文件

| 当前文件 | 目标文件 | 用途 |
|------|------|------|
| `session/<session_id>/left.jsonl` | `session/<session_id>/brain.jsonl` | 用户与大脑的原始对话流 |
| `session/<session_id>/right.jsonl` | `session/<session_id>/executor.jsonl` | 执行层受理、进展、结果等内部记录 |

breaking 规则：

- 新代码只认 `brain.jsonl` 与 `executor.jsonl`
- 不再自动读取 `left.jsonl` 与 `right.jsonl`
- 如果历史数据要迁移，单独执行迁移脚本或批量重命名，不在运行时做兼容

### 4.2 世界模型文件

目标态建议新增：

```text
session/<session_id>/world_model.json
```

用途：

- 保存共享运行态
- 只存当前任务系统事实
- 不混入人格、长期记忆、工具清单、环境通知

### 4.3 记忆文件

| 路径 | 定位 |
|------|------|
| `memory/cognitive_events.jsonl` | 短期认知事件流 |
| `memory/memory.jsonl` | 长期记忆唯一事实源 |
| `memory/vector/` | 长期记忆检索镜像 |

不建议保留额外平行的短期记忆文件夹作为第二事实源。

---

## 5. 世界模型边界

世界模型只保存运行态，不保存人格和上下文拼装材料。

建议字段如下：

```json
{
  "schema_version": "world_model.v1",
  "session_id": "cli:direct",
  "updated_at": "2026-03-20T12:00:00+08:00",
  "current_topic": "修复 reflection 模块 bug",
  "current_task_id": "task_1",
  "tasks": [
    {
      "task_id": "task_1",
      "goal": "修复 reflection 模块 bug",
      "mainline": [
        "看问题",
        ["修改代码", "补测试"],
        "执行测试，看日志"
      ],
      "current_stage": ["修改代码", "补测试"],
      "current_checks": [
        "修改 manager.py，让 deep reflection 写入 user_updates 和 soul_updates",
        "补 governor 相关测试"
      ],
      "last_result": "已定位到 manager.py，开始并行改代码和补测试",
      "check_history": [],
      "artifacts": [],
      "created_at": "2026-03-20T11:50:00+08:00",
      "updated_at": "2026-03-20T12:00:00+08:00"
    }
  ]
}
```

明确不放进去的内容：

- `USER.md`
- `SOUL.md`
- 工具清单
- delivery mode
- runtime 通知事件
- pending event 队列
- ended task 历史堆栈

这些都属于 context 层或 runtime 事件层，不属于 world model。

---

## 6. 单轮运行流程

### 6.1 用户事件

1. 用户输入进入系统
2. Brain 读取 context + world model
3. Brain 输出：
   - `#####user######`
   - `#####Action######`
4. 前台先把 `#####user######` 流式或一次性发给用户
5. 如果 `Action.type = execute`，把当前 `check` 发给 Executor
6. 如果 `Action.type = reflect`，触发浅反思

### 6.2 执行结果事件

1. Executor 完成或失败
2. Executor 只回写事实：
   - 执行结果
   - 产物
   - 错误
   - 建议下一步线索
3. Brain 再次被触发
4. Brain 根据同一个 `goal -> mainline -> current_stage -> current_checks -> last_result` 主线，决定是否继续给下一个 `check`

关键原则：

- 失败时优先改 `check`
- 不因单次失败重写 `goal`
- 用户明确改目标时，结束旧任务并创建新任务

---

## 7. 并行策略

并行不是额外的“子任务系统”，而是主线内部的一种阶段表达。

例子：

```json
[
  "看问题",
  ["修改代码", "补测试"],
  "执行测试，看日志"
]
```

此时：

- `current_stage` 可以是数组
- `current_checks` 可以有多条
- Executor 可以并行跑多个 check
- World Model 只需要记录这些 check 的结果，不需要再造一套复杂状态机

---

## 8. 反思链路

### 8.1 大脑只决定浅反思

Brain 在当前轮判断是否触发：

```json
{
  "type": "reflect",
  "mode": "turn"
}
```

### 8.2 浅反思决定是否继续深反思

浅反思结果应包含：

```json
{
  "needs_deep_reflection": true,
  "user_updates": [],
  "soul_updates": []
}
```

规则：

- 浅反思永远要终结，不能“什么都不产出”
- 深反思不是升级状态，而是浅反思判断“当前是否还需要继续深入”
- 浅反思、深反思都可以产出：
  - `user_updates`
  - `soul_updates`

---

## 9. 按模块推进顺序

建议按下面顺序切，不要并行混切：

1. `session`
   - 先统一会话落盘命名
   - 去掉 `left/right` 历史 API
   - 让 `brain/executor` 成为唯一入口
2. `brain + context`
   - 把 `left_brain` 的提示词、输出协议、上下文构建拆清
   - `context` 从脑模块中独立出来
3. `executor`
   - 把 `right_brain` 改成纯执行层
   - 去掉任何“第二人格”叙事
4. `world_model`
   - 新建独立目录和持久化
   - 把运行态从 session 和记忆逻辑中抽出来
5. `runtime + protocol`
   - 统一 bus、runtime、event 命名
   - 让 brain/executor/world_model 的边界在总线上闭环
6. `reflection + memory`
   - 最后收口反思触发、长期记忆、结晶逻辑
   - 避免在主通路未稳定前反复返工

每个模块都执行同一套动作：

1. 改目录或命名
2. 改调用点
3. 改测试
4. 改文档
5. 再进入下一模块

---

## 10. 一次性重构时的建议替换点

如果准备整体重构，建议一次性替换以下边界，而不是半新半旧混用太久：

1. 命名边界
   - `left/right` 全部替换为 `brain/executor`
2. 持久化边界
   - 会话文件改为 `brain.jsonl / executor.jsonl`
   - 新增 `world_model.json`
3. 提示词边界
   - 统一使用单大脑提示词
4. 运行时边界
   - Brain 只产出用户回复和动作
   - Executor 只执行 check
5. 反思边界
   - Brain 决定 turn reflection
   - turn reflection 决定 deep reflection

---

## 11. 当前推荐起手模块

如果从当前仓库直接开砍，建议第一刀就是：

1. `session/thread_store.py`
2. `bootstrap.py` 里的会话历史读写
3. 相关测试
4. README 与架构文档里的持久化命名

原因很简单：

- 这一层最容易污染整体心智模型
- 改完后，后续 brain/executor 重命名会顺很多
- 也是最适合做成 breaking refactor 的第一块
