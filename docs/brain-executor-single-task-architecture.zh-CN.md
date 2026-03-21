# 单任务 Brain + Executor 架构

## 1. 当前定义

这份文档描述当前单任务实现：

- 只有一个 `Brain`
- 只有一个 `Executor`
- 只有一个 `current_task`
- 并行只存在于 `current_task.current_checks`
- `Brain` 串行决策
- `Executor` 异步执行
- `Executor` 只回填事实，不直接对用户说话

这个版本明确放弃“多任务池 + focus task”的心智模型，主流程保持单任务闭环。

---

## 2. 主流程

唯一主线如下：

1. 用户事件进入 `brain_queue`
2. `Brain` 读取上下文和 `world model`
3. `Brain` 输出：
   - `#####user######`
   - `#####Action######`
4. 如果本轮需要执行，则生成当前任务的一批 `checks`
5. `Executor` 异步执行这批 `checks`
6. 每个 `check` 完成时只回填事实
7. 当当前批次对应的 executor job 进入终态后，回填世界模型
8. `Brain` 再次被唤醒，决定下一批 `checks` 或终结任务

核心原则：

- 不要每个 `check` 完成就让 `Brain` 决策一次
- 只在当前批次终态后让 `Brain` 再决策

---

## 3. 核心组件

### 3.1 Brain

职责：

- 唯一对用户说话
- 稳定当前任务主线
- 决定是否继续执行
- 决定是否触发浅反思
- 判断用户插队属于：
  - `continue`
  - `augment`
  - `replace`

约束：

- 同一 session 内一次只处理一个事件
- 不并发思考
- 不直接执行工具

### 3.2 Executor

职责：

- 执行 `Brain` 给出的当前批次 `checks`
- 异步运行
- 回填执行事实

约束：

- 不直接对用户说话
- 不决定任务主线
- 不决定是否反思
- 不抢占 `Brain`

### 3.3 World Model

职责：

- 保存当前唯一任务的运行态
- 保存当前阶段和当前批次
- 保存最近执行结果

约束：

- 不保存待处理事件
- 不保存人格提示词
- 不保存工具清单
- 不保存运行时回调逻辑

---

## 4. 单任务世界模型

目标结构如下：

```json
{
  "schema_version": "world_model.single_task.v1",
  "session_id": "cli:direct",
  "updated_at": "2026-03-21T12:00:00Z",
  "current_topic": "分析项目架构与风险",
  "current_task": {
    "task_id": "task_001",
    "goal": "分析项目架构与风险",
    "status": "running",
    "summary": "已开始首轮项目扫描。",
    "mainline": [
      "收集项目结构",
      "识别核心模块",
      "分析风险点",
      "输出结论"
    ],
    "current_stage": "收集项目结构",
    "current_batch_id": "batch_001",
    "current_checks": [
      {
        "check_id": "check_001",
        "title": "扫描目录结构",
        "status": "running",
        "result": null,
        "error": null
      },
      {
        "check_id": "check_002",
        "title": "读取 README 和入口文件",
        "status": "pending",
        "result": null,
        "error": null
      }
    ],
    "last_result": "",
    "artifacts": []
  }
}
```

### 4.1 顶层字段

- `schema_version`
  - 世界模型版本
- `session_id`
  - session 标识
- `updated_at`
  - 最近更新时间
- `current_topic`
  - 当前对话主线
- `current_task`
  - 当前唯一任务；无任务时为空

### 4.2 `current_task` 字段

- `task_id`
  - 当前任务 id
- `goal`
  - 当前任务主旨
- `status`
  - 建议只保留：
    - `running`
    - `completed`
    - `failed`
    - `cancelled`
- `summary`
  - 当前任务阶段性摘要
- `mainline`
  - 粗主线，尽量稳定
- `current_stage`
  - 当前阶段
- `current_batch_id`
  - 当前这一批 `checks` 的批次 id
- `current_checks`
  - 当前批次中的执行项
- `last_result`
  - 最近一次执行结果摘要
- `artifacts`
  - 当前任务相关产物

### 4.3 `current_checks` 字段

每个 `check` 建议包含：

- `check_id`
- `title`
- `status`
  - `pending | running | success | failed | cancelled`
- `result`
- `error`

---

## 5. 并行边界

允许并行的部分：

- 多个用户/系统事件并发进入总线
- 一批 `checks` 在 `Executor` 内并行执行

不允许并行的部分：

- 同一 session 内 `Brain` 并行思考
- 多个顶层任务并行争抢主线
- 多个 `Brain` 回复同时发给用户

一句话：

- 事件可以并发到达
- `Brain` 必须串行决策
- 执行层可以并行执行当前批次

---

## 6. 插队规则

单任务版里，插队不是新建任务，而是对当前任务做重新裁决。

### 6.1 `continue`

用户只是聊天、追问、确认。

处理方式：

- 回复用户
- 不改 `current_task`
- `Executor` 继续当前批次

### 6.2 `augment`

用户补充当前任务，但没有改变目标。

处理方式：

- 回复用户
- 更新 `goal` 细节或 `summary`
- 当前批次可继续
- 下一轮批次吸收补充信息

### 6.3 `replace`

用户明确更换目标。

处理方式：

- `Brain` 发中断
- 停止当前批次
- 用新目标替换 `current_task`
- 生成新的批次

---

## 7. 渐进式披露

这个版本采用渐进式披露。

### 7.1 对用户

每轮只披露：

- 当前理解
- 当前正在做的一步
- 这一步完成后再决定下一步

不要一开始倒出完整内部规划。

### 7.2 对世界模型

世界模型允许保存粗主线，但每轮只展开一个 `current_stage` 和一批 `current_checks`。

### 7.3 对执行层

`Executor` 只拿当前批次，不拿整个大计划。

---

## 8. Brain 输出约束

`Brain` 只允许输出两段：

```text
#####user######
<给用户看的自然语言>

#####Action######
<合法 JSON>
```

单任务版要求：

- 一轮最多一个 `execute`
- 如果只是对话，输出：

```json
{"type":"none"}
```

- 如果要推进当前任务，输出：

```json
{
  "type": "execute",
  "task_id": "task_001",
  "goal": "分析项目架构与风险",
  "mainline": [
    "收集项目结构",
    "识别核心模块",
    "分析风险点",
    "输出结论"
  ],
  "current_stage": "识别核心模块",
  "current_checks": [
    "定位核心包与入口",
    "识别模块边界"
  ]
}
```

- 如果要触发浅反思，输出：

```json
{"type":"reflect","mode":"turn"}
```

- 如果同时继续执行和触发浅反思，可以输出数组，但仍然只允许一个 `execute`

明确禁止：

- 一轮多个 `execute`
- 一轮多个独立任务
- 在 `#####user######` 里假装执行已经完成

---

## 9. Executor 事件规则

`Executor` 只需要三类终态事实：

- `success`
- `failed`
- `cancelled`

不需要保留噪音事件：

- 冗长 progress 广播
- 多层 fallback
- 旧式 follow-up turn
- 与主线无关的中间播报

推荐行为：

1. `check` 开始时更新状态为 `running`
2. `check` 结束时回填 `result` 或 `error`
3. 当当前批次全部终态后，发布一次 executor 终态结果事件，由 `session` 唤醒 `Brain`

---

## 10. 反思规则

反思权只属于 `Brain`。

规则：

1. 是否触发浅反思，由 `Brain` 决定
2. 浅反思完成后，如果返回 `needs_deep_reflection=true`，再进入深反思
3. `Executor` 不直接触发反思
4. 反思不改变“`Executor` 只执行 / `Brain` 只决策”这条主线

---

## 11. 当前判断标准

当前实现应满足：

- 世界模型里只有一个 `current_task`
- 用户一轮输入只会得到一个主脑决策
- `Executor` 不会主动对用户说话
- 一批 `checks` 可以并行
- `Brain` 只在整批结束后继续决策
- 用户插队时，只会触发：
  - `continue`
  - `augment`
  - `replace`

这就是单任务版本的完成标准。
