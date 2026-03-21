# 单任务 Brain + Executor 架构概览

当前实现以单任务主线为准，不再使用旧的多任务池叙事。

详细版请看 [brain-executor-single-task-architecture.zh-CN.md](./brain-executor-single-task-architecture.zh-CN.md)。

## 1. 核心原则

- 只有一个 `Brain`
- 只有一个 `Executor`
- 只有一个 `current_task`
- 并行只存在于 `current_task.current_checks`
- `Executor` 只回填事实，不直接对用户说话

唯一主流程：

`用户事件 -> brain 决策 -> executor 执行当前 batch -> world model 回填 -> brain 再决策`

## 2. Brain 职责

`Brain` 每次被唤醒都只做这几件事：

1. 读取上下文和 `world model`
2. 判断当前任务主线是否继续成立
3. 输出给用户的话
4. 决定是否触发浅反思
5. 决定是否下发下一批 `checks`

约束：

- 同一 session 内串行决策
- 一轮最多一个 `execute` action
- 可以和 `reflect` 组合，但不能一轮多个独立任务

## 3. Executor 职责

`Executor` 只负责执行当前批次 `checks`。

它可以并行执行同一批里的多个 `checks`，但不能：

- 改任务主旨
- 决定是否反思
- 直接对用户说话
- 抢占主线

## 4. World Model

当前世界模型是单任务结构：

```json
{
  "schema_version": "world_model.single_task.v1",
  "current_topic": "当前对话主线",
  "current_task": {
    "task_id": "task_001",
    "goal": "任务主旨",
    "status": "running",
    "summary": "阶段性摘要",
    "mainline": ["步骤1", "步骤2", "步骤3"],
    "current_stage": "当前阶段",
    "current_batch_id": "job_xxx",
    "current_checks": [
      {
        "check_id": "check_001",
        "title": "当前 check",
        "status": "pending"
      }
    ],
    "last_result": "最近一次批次结论",
    "check_history": [],
    "artifacts": []
  }
}
```

判断主线时优先沿着这一条读：

`goal -> mainline -> current_stage -> current_checks -> last_result`

## 5. 输出协议

`Brain` 必须且只能输出两段：

```text
#####user######
<给用户看的自然语言回复>

#####Action######
<合法 JSON>
```

允许的动作只有：

- `{"type":"none"}`
- `{"type":"execute", ...}`
- `{"type":"reflect","mode":"turn"}`

补充规则：

- 如果只是继续对话，输出 `none`
- 如果要执行，输出一个 `execute`
- 如果要终止当前任务，可输出 `{"type":"execute","operation":"cancel","task_id":"当前任务 id","reason":"..."}`
- 如果既要继续执行又要浅反思，可以输出数组，但数组里最多一个 `execute`

## 6. 插队规则

用户在任务中途插入新信息时，只允许三种判断：

- `continue`
  - 只是追问、确认、闲聊，不改当前任务
- `augment`
  - 仍是同一个目标，只补充条件，下一批 `checks` 吸收进去
- `replace`
  - 用户明确换目标，结束旧任务，切换到新任务

## 7. 明确移除的旧产物

当前主线里不再保留这些概念：

- 多个顶层任务并行争抢主线
- 一轮多个 `execute`
- `followup turn`
- `accepted / progress` 噪音播报
- `left brain / right brain` 双主体叙事
