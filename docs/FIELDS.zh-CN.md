# emoticorebot 字段规范

本文档定义当前目标架构下的字段结构、字段语义与记录边界。

边界、职责、流程请看 `ARCHITECTURE.zh-CN.md`；字段语义、准入规则与存储边界以本文为准。

---

## 1. 适用范围

当前覆盖：

- `session` 外部原始记录
- `internal` 内部原始记录
- `runtime_event`
- `brain` 运行时字段
- `task`
- `task_context`
- `task_control_command`
- `task_event`
- `cognitive_event`
- `turn_reflection`
- `deep_reflection`
- 统一长期 `memory`
- 向量索引层的辅助字段

当前不展开：

- `vision`
- `voice`
- 其他未来感知输入
- 具体 `skills` 文件内部结构

---

## 2. 兼容命名与废弃命名

为便于迁移，本文使用以下兼容命名：

- `turn_reflection` == 旧命名 `light_insight`
- `deep_reflection` == 旧命名 `deep_insight`

架构角色名与字段值约定：

- 本文正式命名统一使用 `brain` 与 `central`
- 当前代码实现名、字段枚举值、事件记录值统一使用 `central`
- `central` 对应当前代码落点 `emoticorebot/agent/central/central.py`
- `specialized agents` 在字段里写具体 agent 名，代码目录统一落在 `agent/central/subagent/`

以下命名已从顶层架构中废弃：

- `executor`
- `executor_context`
- `executor_trace`

迁移建议：

- 旧 `executor`：
  - 按语义迁移为 `task system` 或 `central`
- 旧 `executor_context`：
  - 迁移为 `task_context`
- 旧 `executor_trace`：
  - 迁移为 `task.events`

说明：

1. `turn_reflection` 与 `deep_reflection` 是反思机制，不是独立长期存储层
2. 长期沉淀统一进入一个长期 `memory` 存储
3. `task` 是复杂任务的一等公民对象
4. `brain` 是唯一主体

---

## 3. 总体约束

1. 系统只有一个主体：`brain`
2. `task system`、`central`、`specialized agents` 都不是独立主体
3. `session` 只保存外部原始运行时材料，不保存长期解释结论
4. `internal` 保存主脑与机器系统之间的内部记录
5. `task.events` 保存任务过程日志与主脑可读关键步骤
6. `task.result` 保存任务终态结果
7. `task` 从属于 `session`，不跨 `session`
8. `RuntimeEventBus` 是统一运行时通信机制，`runtime_event` 是其标准传输对象
9. `task_event` 与 `task_control_command` 是任务域的标准业务载荷，可通过 `RuntimeEventBus` 传递
10. 长期记忆只有一个统一存储：`memory`
11. 长期 `memory` 的人类可读源为 `memory.jsonl`
12. 向量库是 `memory.jsonl` 的检索镜像，不是语义源头
13. `brain` 是唯一长期记忆直接检索者
14. `task system` 与子 agent 不直接决定长期记忆解释，也不拥有自己的记忆层
15. `brain` 对任务拥有创建、暂停、恢复、改向、终止与接管权
16. 每轮结束都应至少产生一次 `turn_reflection`
17. `turn_reflection` 可把高置信用户信息、主脑风格修正和小幅 `state_update` 直接写回托管锚点或状态文件
18. `deep_reflection` 负责阶段性归纳、用户整体评估、技能候选沉淀
19. 长期 `memory` 只保存蒸馏后的稳定价值，不保存原始大段对话或大段工具输出
20. 已稳定沉淀到 `skills` 的能力，不重复把完整技能内容写入 `memory`，只保存 `skill_hint`

---

## 4. 分层原则

### 4.1 分层总表

| 层级 | 代表对象 | 生命周期 | 保存内容 | 不保存内容 |
| --- | --- | --- | --- | --- |
| 原始层 | `session / internal / checkpointer` | 当前轮到若干轮 | 原始对话、控制动作、暂停恢复现场 | 长期解释结论 |
| 过程层 | `task.events / task.result` | 任务存续期 | 主脑可读的进展、里程碑、阻塞、风险与终态结果 | 完整原始工具日志 |
| 认知层 | `cognitive_event` | 持续累积 | 主脑视角下的一轮结构化认知切片 | 大量原始日志 |
| 反思层 | `turn_reflection / deep_reflection` | 每轮或按需/周期产生 | 本轮解释、阶段归纳、候选长期结论 | 最终长期存储真身 |
| 长期层 | `memory` | 长期 | 已蒸馏的稳定事实、经验、模式、提示 | 原始日志、完整续跑状态、完整技能正文 |
| 索引层 | `memory/chroma/` | 长期 | 检索辅助字段、向量、访问统计 | 人类可读语义源 |

### 4.2 基本规则

1. 原始层与长期层必须分离
2. 过程层服务于主脑持续知情，不替代原始日志
3. 反思层可以产出候选长期记忆，但不等于长期记忆本身
4. 长期更新采用追加式写入；若修正旧结论，应新增记录并通过 `links.supersedes` 或 `links.invalidates` 建链
5. 一条长期记忆只表达一个清晰语义单元，不混写多个不相干结论

---

## 5. 原始记录

### 5.1 `session` 外部原始记录

定位：用户与 `brain` 的外部对话原始材料，供回放、恢复、审计与短期上下文使用。

建议结构：

```json
{
  "id": "sess_evt_xxx",
  "timestamp": "2026-03-11T10:10:00+08:00",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "actor": "user",
  "event_type": "message",
  "content": "帮我看看这个错误",
  "raw_payload": {},
  "meta": {}
}
```

#### 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 原始事件 ID |
| `timestamp` | 时间戳 |
| `session_id` | 会话 ID |
| `turn_id` | 当前轮次 ID |
| `actor` | `user / assistant / system` |
| `event_type` | `message / note / interruption / summary` |
| `content` | 原始文本或简要原始内容 |
| `raw_payload` | 原始结构化载荷 |
| `meta` | 附加元数据 |

#### 约束

1. `session` 不写 `importance / confidence / stability`
2. `session` 只保存外部交互，不承担任务内部原始日志
3. `session` 可用于恢复对话现场，但不应被当作长期认知结论

### 5.2 `internal` 内部原始记录

定位：`brain` 与机器运行层之间的内部原始交互记录。

建议结构：

```json
{
  "id": "int_evt_xxx",
  "timestamp": "2026-03-11T10:11:00+08:00",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "task_id": "task_xxx",
  "actor": "brain",
  "target": "task_system",
  "event_type": "task_create",
  "content": "创建异步任务，继续后台分析",
  "raw_payload": {},
  "meta": {}
}
```

#### 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 内部事件 ID |
| `timestamp` | 时间戳 |
| `session_id` | 会话 ID |
| `turn_id` | 当前轮次 ID |
| `task_id` | 关联任务 ID，可为空 |
| `actor` | `brain / task_system / central / specialized_agent / system` |
| `target` | `brain / task_system / task / central / specialized_agent` |
| `event_type` | `task_create / task_control / task_notice / memory_retrieval / narrative_note / system_note` |
| `content` | 简短内部说明 |
| `raw_payload` | 原始结构化数据 |
| `meta` | 附加元数据 |

#### 约束

1. `internal` 不是主脑最终叙述文本
2. `internal` 可以保存控制动作与任务通知
3. `internal` 不应替代 `task.events`

### 5.3 `runtime_event`

定位：`RuntimeEventBus` 上流动的统一运行时事件封装。

它只负责传输，不直接等于长期存储对象。

当前阶段它是进程内传输对象，不作为外部消息中间件协议。

建议结构：

```json
{
  "event_id": "rt_evt_xxx",
  "channel": "telegram",
  "session_id": "sess_xxx",
  "task_id": "task_xxx",
  "source": "task_system",
  "target": "brain",
  "kind": "task_event",
  "event_type": "task_stage_changed",
  "payload": {},
  "created_at": "2026-03-11T10:15:00+08:00"
}
```

#### 字段

| 字段 | 含义 |
| --- | --- |
| `event_id` | 运行时事件 ID |
| `channel` | 外部通道，例如 `telegram / discord / cli` |
| `session_id` | 所属会话 ID |
| `task_id` | 所属任务 ID；非任务事件可为空 |
| `source` | 事件发出方，例如 `user / brain / task_system / channel_adapter / system` |
| `target` | 事件接收方，例如 `brain / task_system / user / channel_adapter / system` |
| `kind` | 事件大类，建议为 `user_message / outbound_message / task_event / task_command / system_event` |
| `event_type` | 具体事件类型，例如 `message_received / task_progress / pause_task / task_completed` |
| `payload` | 具体业务载荷 |
| `created_at` | 事件创建时间 |

#### 约束

1. `channel` 保持外部通道语义，不承担内部业务路由语义
2. 内部业务路由主要依赖 `kind / source / target`
3. 所有运行时事件必须明确 `session_id`
4. 所有任务相关运行时事件必须明确 `task_id`
5. `runtime_event` 是传输封装；需要持久化时，再分别投影到 `session`、`internal`、`task.events`、`task.result` 等对象
6. `task system` 不直接发布面向用户的最终表达；真正的 `outbound_message` 由 `brain` 决定
7. `RuntimeEventBus` 当前设计为当前进程内总线，不依赖外部调度框架或外部消息基础设施

---

## 6. 运行时状态字段

### 6.1 `brain`

定位：唯一主体的当前轮运行时状态。

建议结构：

```json
{
  "emotion": "平静",
  "pad": {
    "pleasure": 0.10,
    "arousal": 0.45,
    "dominance": 0.58
  },
  "intent": "",
  "working_hypothesis": "",
  "retrieval_query": "",
  "retrieval_focus": [],
  "retrieved_memory_ids": [],
  "task_action": "none",
  "task_target_ids": [],
  "task_reason": "",
  "subject_narrative": "",
  "active_task_ids": [],
  "attended_task_ids": [],
  "final_decision": "answer",
  "final_message": ""
}
```

| 字段 | 含义 |
| --- | --- |
| `emotion` | 当前主脑情绪标签 |
| `pad` | 当前主脑 `PAD` 状态 |
| `intent` | 本轮对用户意图的理解 |
| `working_hypothesis` | 当前工作性判断 |
| `retrieval_query` | 当前检索查询文本 |
| `retrieval_focus` | 检索关注点，如 `user / relationship / task / skill` |
| `retrieved_memory_ids` | 本轮实际命中的长期记忆 ID |
| `task_action` | `none / create_sync_task / create_async_task / pause_task / resume_task / steer_task / cancel_task / takeover_task / request_report` |
| `task_target_ids` | 本轮操作涉及的任务 ID 集合 |
| `task_reason` | 采取该任务动作的原因 |
| `subject_narrative` | 主脑当前对外可叙述的过程状态 |
| `active_task_ids` | 当前仍存活的任务 ID |
| `attended_task_ids` | 本轮主动关注过的任务 ID |
| `final_decision` | `answer / ask_user / wait_task / continue_dialogue / task_controlled` |
| `final_message` | 最终对外回复 |

### 6.2 `task`

定位：复杂任务的一等公民对象。

建议结构：

```json
{
  "task_id": "task_xxx",
  "session_id": "sess_xxx",
  "source_turn_id": "turn_xxx",
  "title": "对齐架构与字段文档",
  "goal": "将字段文档改为 task system 架构",
  "status": "running",
  "priority": "high",
  "mode": "async",
  "owner_agent": "central",
  "created_by": "brain",
  "active_specialized_agents": [],
  "current_stage": "rewrite_fields",
  "plan_summary": "",
  "requires_attention": false,
  "need_user_input": false,
  "context_notes": [],
  "events": [],
  "result": {
    "status": "pending",
    "summary": "",
    "detailed_result": "",
    "risks": [],
    "missing": [],
    "recommended_action": "continue_task",
    "confidence": 0.0
  },
  "created_at": "2026-03-11T10:12:00+08:00",
  "updated_at": "2026-03-11T10:14:00+08:00",
  "finished_at": null
}
```

| 字段 | 含义 |
| --- | --- |
| `task_id` | 任务唯一 ID |
| `session_id` | 所属会话 ID，任务不跨 `session` |
| `source_turn_id` | 发起该任务的源轮次 ID |
| `title` | 任务简名 |
| `goal` | 当前任务目标 |
| `status` | `pending / running / waiting_input / blocked / paused / completed / failed / cancelled` |
| `priority` | `low / medium / high / critical` |
| `mode` | `sync / async` |
| `owner_agent` | 当前固定为 `central` |
| `created_by` | 通常是 `brain` |
| `active_specialized_agents` | 当前参与任务的专项 agent 名称列表 |
| `current_stage` | 当前高层阶段标识 |
| `plan_summary` | 当前任务的高层计划摘要 |
| `requires_attention` | 是否需要主脑立即关注 |
| `need_user_input` | 是否需要用户或主脑补充信息 |
| `context_notes` | 主脑传入的临时上下文摘要 |
| `events` | 当前任务过程事件列表 |
| `result` | 当前任务终态结果；未完成时为 `pending` |
| `created_at` | 创建时间 |
| `updated_at` | 最近更新时间 |
| `finished_at` | 完成、失败或取消时间；未结束时为 `null` |

`current_stage` 约束：

- 表示任务当前的高层工作状态
- 不是单次工具调用或单条原始日志
- 只在任务进入新的稳定工作状态时变化

### 6.3 `task_context`

定位：`brain` 检索并裁剪后，传给 `task system / central` 的任务上下文包。

建议结构：

```json
{
  "goal": "",
  "request": "",
  "constraints": [],
  "context_notes": [],
  "skill_hints": [],
  "success_criteria": [],
  "allowed_agents": [],
  "return_contract": {
    "mode": "event_and_final",
    "must_not": [
      "direct_user_reply",
      "direct_memory_write"
    ]
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `goal` | 当前任务目标 |
| `request` | 主脑交付给任务系统的内部问题 |
| `constraints` | 用户约束、环境约束、权限约束 |
| `context_notes` | `brain` 提炼后传给任务的相关信息摘要，不等于任务自己的记忆 |
| `skill_hints` | 已沉淀技能的触发提示 |
| `success_criteria` | 成功标准 |
| `allowed_agents` | 当前任务允许调用的专项 agent 白名单 |
| `return_contract` | 结果返回方式与禁止事项 |

### 6.4 `task_control_command`

定位：`brain` 对任务发出的结构化控制命令。

它是 `RuntimeEventBus` 上 `kind=task_command` 的标准任务控制载荷。

建议结构：

```json
{
  "command_id": "cmd_xxx",
  "session_id": "sess_xxx",
  "task_id": "task_xxx",
  "command": "pause_task",
  "reason": "用户临时切换话题",
  "payload": {},
  "issued_by": "brain",
  "created_at": "2026-03-11T10:20:00+08:00"
}
```

| 字段 | 含义 |
| --- | --- |
| `command_id` | 控制命令 ID |
| `session_id` | 所属会话 ID |
| `task_id` | 目标任务 ID |
| `command` | `pause_task / resume_task / cancel_task / steer_task / reprioritize_task / request_report / takeover_task` |
| `reason` | 发出该命令的原因 |
| `payload` | 命令附带参数，例如新方向、优先级、补充要求 |
| `issued_by` | 默认是 `brain` |
| `created_at` | 发出时间 |

常见 `event_type`：

- `pause_task`
- `resume_task`
- `cancel_task`
- `steer_task`
- `reprioritize_task`
- `request_report`
- `takeover_task`

## 7. 任务过程对象

### 7.1 `task_event`

定位：任务过程中的结构化步骤事件，也是主脑持续知情的最小单元。

它是 `RuntimeEventBus` 上 `kind=task_event` 的标准任务过程载荷。

建议结构：

```json
{
  "event_id": "task_evt_xxx",
  "session_id": "sess_xxx",
  "task_id": "task_xxx",
  "event_type": "task_progress",
  "by": "central",
  "stage": "rewrite_fields",
  "action": "重写字段文档中的运行时对象定义",
  "reason": "旧文档仍以 executor 与旧目录语义为中心",
  "result": "已完成 brain、task 与 task_context 字段定义",
  "next_action": "继续补 task_event 与 memory 类型",
  "priority": "medium",
  "requires_attention": false,
  "created_at": "2026-03-11T10:16:00+08:00"
}
```

| 字段 | 含义 |
| --- | --- |
| `event_id` | 任务事件 ID |
| `session_id` | 所属会话 ID |
| `task_id` | 任务 ID |
| `event_type` | 任务事件类型 |
| `by` | 当前步骤由谁产生，通常是 `central` 或某个 specialized agent |
| `stage` | 当前阶段标识 |
| `action` | 这一步做了什么 |
| `reason` | 为什么要做这一步 |
| `result` | 这一步得到了什么 |
| `next_action` | 下一步准备做什么 |
| `priority` | `low / medium / high / critical` |
| `requires_attention` | 是否需要主脑立即处理 |
| `created_at` | 事件时间 |

`stage` 约束：

- 记录该事件所属的高层工作阶段
- 不应把每次底层工具调用都提升为新的 `stage`
- 只有阶段切换或关键阶段内的重要事件，才值得写入 `task_event`

常见 `event_type`：

- `task_created`
- `task_started`
- `task_planned`
- `task_progress`
- `task_stage_changed`
- `task_blocked`
- `task_need_user_input`
- `task_risk_alert`
- `task_partial_result`
- `task_completed`
- `task_failed`
- `task_cancelled`

约束：

1. `task_event` 默认是阶段级、里程碑级、阻塞级与结果级事件
2. 不应把每次底层工具调用都上推为 `task_event`

---

## 8. `cognitive_event`

定位：主脑视角下的一轮结构化认知切片。

建议结构：

```json
{
  "id": "evt_xxx",
  "version": "4",
  "timestamp": "2026-03-11T10:20:00+08:00",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "user_input": "用户原始输入",
  "brain_state": {},
  "retrieval": {
    "query": "",
    "memory_ids": []
  },
  "task": {
    "created": false,
    "controlled": false,
    "active_task_ids": [],
    "consumed_task_event_ids": [],
    "summary": ""
  },
  "assistant_output": "",
  "turn_reflection": {},
  "meta": {}
}
```

| 字段 | 含义 |
| --- | --- |
| `id` | 认知事件 ID |
| `version` | 结构版本 |
| `timestamp` | 时间戳 |
| `session_id` | 会话 ID |
| `turn_id` | 轮次 ID |
| `user_input` | 用户本轮输入 |
| `brain_state` | 主脑状态切片 |
| `retrieval.query` | 主脑检索查询 |
| `retrieval.memory_ids` | 本轮命中的长期记忆 ID |
| `task.created` | 本轮是否创建了新任务 |
| `task.controlled` | 本轮是否控制了既有任务 |
| `task.active_task_ids` | 本轮仍相关的任务 ID |
| `task.consumed_task_event_ids` | 本轮主脑实际消费过的任务事件 ID |
| `task.summary` | 本轮与任务相关的高层摘要 |
| `assistant_output` | 主脑最终对外回复 |
| `turn_reflection` | 本轮轻反思结果 |
| `meta` | 附加元数据 |

---

## 9. 反思字段

### 9.1 `turn_reflection`

定位：每轮结束后的即时轻反思，也是本轮快速直写的结构化来源。

建议结构：

```json
{
  "summary": "",
  "problems": [],
  "resolution": "",
  "outcome": "success",
  "next_hint": "",
  "user_updates": [],
  "soul_updates": [],
  "state_update": {
    "should_apply": false,
    "confidence": 0.0,
    "reason": "",
    "pad_delta": {},
    "drives_delta": {}
  },
  "memory_candidates": [],
  "task_review": {
    "task_ids": [],
    "effectiveness": "medium",
    "main_blocker": "",
    "missing_inputs": [],
    "next_task_hint": ""
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `summary` | 本轮发生了什么的简要洞察 |
| `problems` | 本轮暴露的问题列表 |
| `resolution` | 问题最终如何被解决 |
| `outcome` | `success / partial / failed / no_task` |
| `next_hint` | 下一轮主脑如何承接 |
| `user_updates` | 本轮可直接写入 `USER.md` 托管锚点块的高置信用户信息候选 |
| `soul_updates` | 本轮可直接写入 `SOUL.md` 托管锚点块的高置信主脑风格候选 |
| `state_update` | 对 `current_state.md` 的小幅增量更新建议 |
| `memory_candidates` | 本轮拟写入长期 `memory` 的候选记录 |
| `task_review` | 对本轮任务过程的评价 |

`state_update` 约束：

| 字段 | 含义 |
| --- | --- |
| `should_apply` | 是否建议应用本次状态增量 |
| `confidence` | 置信度，低于阈值时不应应用 |
| `reason` | 应用该状态微调的原因 |
| `pad_delta` | `pleasure / arousal / dominance` 的小幅增量 |
| `drives_delta` | `social / energy` 的小幅增量 |

### 9.2 `deep_reflection`

定位：按需或周期触发的深反思。

建议结构：

```json
{
  "summary": "",
  "memory_candidates": [],
  "user_updates": [],
  "soul_updates": [],
  "skill_hints": [
    {
      "summary": "",
      "content": "",
      "trigger": "",
      "hint": "",
      "skill_name": ""
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `summary` | 一个阶段的高层总结 |
| `memory_candidates` | 拟写入长期 `memory` 的候选记录 |
| `user_updates` | 对用户整体画像的稳定更新候选，可写入 `USER.md` 深反思锚点块 |
| `soul_updates` | 对主脑稳定风格的更新候选，可写入 `SOUL.md` 深反思锚点块 |
| `skill_hints` | 值得沉淀为 `skill_hint` 的任务提示候选 |

---

## 10. 统一长期 `memory`

定位：统一长期记忆层。

存储模型：

- `memory.jsonl`
  - 人类可读、可审计、追加式写入的源存储
- `memory/chroma/`
  - 面向检索的 Chroma 镜像层
- 两者通过同一个 `memory.id` 对齐

### 10.1 记录结构

建议结构：

```json
{
  "schema_version": "memory.v1",
  "id": "mem_xxx",
  "created_at": "2026-03-11T10:21:00+08:00",
  "audience": "shared",
  "kind": "episodic",
  "type": "turn_insight",
  "summary": "",
  "content": "",
  "importance": 7,
  "confidence": 0.92,
  "stability": 0.55,
  "status": "active",
  "tags": [],
  "source": {},
  "links": {},
  "payload": {},
  "expires_at": null,
  "metadata": {}
}
```

### 10.2 顶层公共字段

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `schema_version` | `string` | 是 | 当前固定为 `memory.v1` |
| `id` | `string` | 是 | 记忆唯一 ID |
| `created_at` | `string` | 是 | 生成时间，ISO 8601 |
| `audience` | `string` | 是 | 记忆主要面向谁使用 |
| `kind` | `string` | 是 | 记忆性质 |
| `type` | `string` | 是 | 记忆具体类型 |
| `summary` | `string` | 是 | 一句话摘要 |
| `content` | `string` | 是 | 蒸馏后的完整内容 |
| `importance` | `int` | 是 | 重要性，范围 `1-10` |
| `confidence` | `float` | 是 | 可信度，范围 `0-1` |
| `stability` | `float` | 是 | 稳定度，范围 `0-1` |
| `status` | `string` | 是 | 当前状态 |
| `tags` | `string[]` | 否 | 标签集合 |
| `source` | `object` | 是 | 来源信息 |
| `links` | `object` | 否 | 关联信息 |
| `payload` | `object` | 是 | 类型扩展字段 |
| `expires_at` | `string|null` | 否 | 过期时间 |
| `metadata` | `object` | 否 | 额外元数据 |

### 10.3 `audience`

| 值 | 含义 |
| --- | --- |
| `brain` | 主要供主脑理解用户、关系、自我与长期判断使用 |
| `shared` | 可支持主脑理解，也可由主脑裁剪后转交给任务使用 |

### 10.4 `kind`

| 值 | 含义 |
| --- | --- |
| `episodic` | 事件性记忆，强调某一轮、某一次经历、某一次解决过程 |
| `durable` | 稳定性记忆，强调较长期不易变化的事实、偏好、关系、画像 |
| `procedural` | 程序性记忆，强调方法、模式、经验、技能提示 |

### 10.5 `status`

| 值 | 含义 |
| --- | --- |
| `active` | 当前有效 |
| `superseded` | 已被更新版本替代 |
| `invalid` | 已确认无效 |
| `expired` | 时间到期后自然失效 |

### 10.6 `type`

#### 用户与关系相关

| `type` | 推荐 `audience` | 推荐 `kind` | 含义 |
| --- | --- | --- | --- |
| `user_fact` | `brain` | `durable` | 用户稳定事实 |
| `preference` | `brain` | `durable` | 用户偏好、厌恶、风格倾向 |
| `goal` | `shared` | `durable` 或 `episodic` | 用户目标或当前任务 |
| `constraint` | `shared` | `durable` 或 `episodic` | 用户边界、限制、禁忌、环境约束 |
| `relationship` | `brain` | `durable` | 用户与人、事、物的关系状态 |
| `soul_trait` | `brain` | `durable` | 主脑长期风格、自我修正与人格锚点 |

#### 任务与经验相关

| `type` | 推荐 `audience` | 推荐 `kind` | 含义 |
| --- | --- | --- | --- |
| `turn_insight` | `shared` | `episodic` | 当前轮发生了什么、问题是什么、怎么解决的 |
| `task_experience` | `brain` 或 `shared` | `procedural` | 某类任务在某种 agent/tool 组合下的执行经验 |
| `error_pattern` | `brain` 或 `shared` | `procedural` | 错误特征到解决方案的模式 |
| `workflow_pattern` | `brain` 或 `shared` | `procedural` | 多 agent / 多工具形成的稳定路径 |
| `skill_hint` | `brain` 或 `shared` | `procedural` | 已沉淀技能的触发提示，而不是技能全文 |

### 10.7 `source`

建议结构：

```json
{
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "task_ids": ["task_a"],
  "event_ids": ["evt_a", "evt_b"],
  "producer": "brain.turn_reflection",
  "agent_names": ["central"],
  "tool_names": ["shell"],
  "model": "gpt-x",
  "trace_id": "trace_xxx"
}
```

### 10.8 `links`

建议结构：

```json
{
  "related_ids": [],
  "evidence_ids": [],
  "entity_ids": [],
  "skill_ids": [],
  "supersedes": [],
  "invalidates": []
}
```

### 10.9 `payload` 扩展字段

#### A. `turn_insight`

```json
{
  "problem": "",
  "task_ids": [],
  "resolution": "",
  "outcome": "success",
  "follow_up": ""
}
```

#### B. `user_fact`

```json
{
  "subject": "user",
  "attribute": "city",
  "value": "杭州",
  "normalized_value": "hangzhou"
}
```

#### C. `preference`

```json
{
  "subject": "user",
  "item": "回复风格",
  "polarity": "like",
  "strength": 0.8,
  "context": "希望直接、少废话"
}
```

#### D. `goal`

```json
{
  "goal": "减少模型交互次数",
  "horizon": "mid",
  "priority": 0.9,
  "progress": "discussing"
}
```

#### E. `constraint`

```json
{
  "constraint": "任务不能直接对用户输出最终回复",
  "level": "hard",
  "scope": "architecture"
}
```

#### F. `relationship`

```json
{
  "target": "assistant",
  "relation": "信任增强",
  "sentiment": "positive",
  "salience": 0.72
}
```

#### G. `soul_trait`

```json
{
  "trait": "强调主体连续性与控制权",
  "direction": "strengthen",
  "basis": "多轮讨论后用户持续强调主脑知情与控制",
  "evidence_count": 6
}
```

#### H. `task_experience`

```json
{
  "task_signature": "字段文档对齐",
  "agent_path": ["central"],
  "tool_names": ["shell", "analysis"],
  "failure_mode": "",
  "resolution": "先整体重写，再回头对齐术语",
  "success": true,
  "latency_hint": "medium",
  "cost_hint": "low"
}
```

#### I. `error_pattern`

```json
{
  "tool_name": "web",
  "error_signature": "403 / blocked request",
  "error_keywords": ["403", "blocked", "forbidden"],
  "resolution": "改走本地资料或请求用户确认权限",
  "sample_size": 4,
  "success_rate": 0.75
}
```

#### J. `workflow_pattern`

```json
{
  "goal_cluster": "文档架构整理",
  "agent_sequence": ["central"],
  "tool_sequence": ["shell", "analysis", "apply_patch"],
  "preconditions": ["本地仓库可读"],
  "steps_summary": "先读现有文档，再整体重写，再补术语与示例",
  "sample_size": 5,
  "success_rate": 0.8
}
```

#### K. `skill_hint`

```json
{
  "skill_id": "skill_architecture_doc_alignment",
  "skill_name": "架构文档对齐",
  "trigger": "当任务要求整体对齐架构与字段文档时",
  "hint": "优先改顶层概念，再统一对象模型与示例",
  "applies_to_tools": ["shell", "analysis", "apply_patch"]
}
```

---

## 11. 向量索引层字段

定位：为长期 `memory` 提供检索能力的镜像层。

当前实现：

- 检索镜像目录：`memory/chroma/`
- 向量后端：`Chroma PersistentClient`
- 事实源头：`memory/memory.jsonl`
- 访问统计 sidecar：`memory/chroma/_access_stats.json`

### 11.1 Chroma 集合镜像字段

| 字段 | 含义 |
| --- | --- |
| `memory_id` | 对应长期记忆 `id` |
| `audience` | 镜像的受众字段 |
| `kind` | 镜像的记忆性质字段 |
| `type` | 镜像的记忆类型字段 |
| `status` | 镜像的状态字段 |
| `tags_text` | 由 `tags` 拼接出的检索文本 |
| `importance` | 从长期记忆镜像过来的重要性 |
| `confidence` | 从长期记忆镜像过来的可信度 |
| `stability` | 从长期记忆镜像过来的稳定度 |
| `created_at` | 创建时间镜像 |
| `expires_at` | 过期时间镜像 |

### 11.2 访问统计 sidecar 字段

`memory/chroma/_access_stats.json` 的记录按 `memory_id` 聚合，建议结构：

```json
{
  "mem_xxx": {
    "recall_count": 3,
    "last_retrieved_at": "2026-03-11T10:30:00+08:00",
    "last_relevance_score": 0.812345
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `recall_count` | 该记忆被最终命中的累计次数 |
| `last_retrieved_at` | 最近一次最终命中的时间 |
| `last_relevance_score` | 最近一次最终命中时的向量相关分数 |

### 11.3 约束

1. 索引层不是人类可读语义源头
2. 索引层可重建，`memory.jsonl` 才是语义源头
3. `task system`、`central` 与 `specialized agents` 不直接查询索引层
4. 当前实现可采用词法分数 + 向量分数的混合排序
5. 访问统计优先放在索引 sidecar，而不是 `memory.jsonl`
6. `last_relevance_score` 是检索统计，不等于长期事实语义

---

## 12. 示例

### 12.1 `turn_insight`

```json
{
  "schema_version": "memory.v1",
  "id": "mem_01",
  "created_at": "2026-03-11T10:31:00+08:00",
  "audience": "shared",
  "kind": "episodic",
  "type": "turn_insight",
  "summary": "本轮完成了字段文档与新架构的对齐",
  "content": "本轮用户要求将字段文档对齐到新的 brain + central 架构。主脑先确认需要整体重写，再创建任务并完成字段对象重构。关键收益是清除了旧 executor 与旧目录语义。后续应继续检查实现代码中的旧命名残留。",
  "importance": 8,
  "confidence": 0.94,
  "stability": 0.62,
  "status": "active",
  "tags": ["doc", "architecture", "migration"],
  "source": {
    "session_id": "sess_x",
    "turn_id": "turn_12",
    "task_ids": ["task_01"],
    "event_ids": ["evt_1", "evt_2"],
    "producer": "brain.turn_reflection",
    "agent_names": ["central"],
    "tool_names": ["shell", "apply_patch"]
  },
  "links": {
    "related_ids": [],
    "evidence_ids": ["evt_1", "evt_2"],
    "entity_ids": [],
    "skill_ids": [],
    "supersedes": [],
    "invalidates": []
  },
  "payload": {
    "problem": "字段文档仍以 executor 和旧目录语义为中心",
    "task_ids": ["task_01"],
    "resolution": "整体重写为 brain + central 对象模型",
    "outcome": "success",
    "follow_up": "继续检查代码与提示词中的旧命名"
  },
  "expires_at": null,
  "metadata": {}
}
```

### 12.2 `task_experience`

```json
{
  "schema_version": "memory.v1",
  "id": "mem_02",
  "created_at": "2026-03-11T10:32:00+08:00",
  "audience": "shared",
  "kind": "procedural",
  "type": "task_experience",
  "summary": "文档重构任务适合先整体改对象表，再回头统一示例与术语",
  "content": "当任务目标是整体对齐架构文档与字段文档时，直接全局重写比局部修补更稳定。先重写顶层对象定义，再统一示例、枚举和值域，通常能减少概念冲突。",
  "importance": 8,
  "confidence": 0.89,
  "stability": 0.74,
  "status": "active",
  "tags": ["task", "doc", "rewrite"],
  "source": {
    "session_id": "sess_x",
    "turn_id": "turn_13",
    "task_ids": ["task_01"],
    "event_ids": ["evt_3"],
    "producer": "brain.turn_reflection",
    "agent_names": ["central"],
    "tool_names": ["shell", "apply_patch"]
  },
  "links": {
    "related_ids": [],
    "evidence_ids": ["evt_3"],
    "entity_ids": [],
    "skill_ids": [],
    "supersedes": [],
    "invalidates": []
  },
  "payload": {
    "task_signature": "字段文档对齐",
    "agent_path": ["central"],
    "tool_names": ["shell", "analysis", "apply_patch"],
    "failure_mode": "",
    "resolution": "先整体重写，再统一术语与示例",
    "success": true,
    "latency_hint": "medium",
    "cost_hint": "low"
  },
  "expires_at": null,
  "metadata": {}
}
```

### 12.3 `skill_hint`

```json
{
  "schema_version": "memory.v1",
  "id": "mem_03",
  "created_at": "2026-03-11T10:33:00+08:00",
  "audience": "shared",
  "kind": "procedural",
  "type": "skill_hint",
  "summary": "当任务要求对齐设计文档时，优先统一概念、对象和示例三层",
  "content": "当任务目标是重写架构相关文档时，优先统一顶层概念，再统一对象模型，最后统一示例与存储落点，通常可以快速消除旧语义残留。",
  "importance": 7,
  "confidence": 0.84,
  "stability": 0.81,
  "status": "active",
  "tags": ["skill", "architecture", "writing"],
  "source": {
    "session_id": "sess_x",
    "turn_id": "turn_20",
    "task_ids": ["task_01"],
    "event_ids": ["evt_7", "evt_8"],
    "producer": "brain.deep_reflection",
    "agent_names": ["central"],
    "tool_names": ["shell", "analysis", "apply_patch"]
  },
  "links": {
    "related_ids": [],
    "evidence_ids": ["evt_7", "evt_8"],
    "entity_ids": [],
    "skill_ids": ["skill_architecture_doc_alignment"],
    "supersedes": [],
    "invalidates": []
  },
  "payload": {
    "skill_id": "skill_architecture_doc_alignment",
    "skill_name": "架构文档对齐",
    "trigger": "当任务要求整体对齐架构与字段文档时",
    "hint": "先统一顶层概念，再统一对象字段，最后统一示例和落点",
    "applies_to_tools": ["shell", "analysis", "apply_patch"]
  },
  "expires_at": null,
  "metadata": {}
}
```

---

## 13. 最终边界总结

1. `session` 只存外部原始数据
2. `internal` 存主脑与机器系统的内部交互
3. `task.events` 存任务过程日志与关键步骤
4. `task.result` 存任务终态结果
5. `task` 从属于 `session`，不跨 `session`
6. `memory` 是统一长期存储
7. `brain` 是唯一长期记忆直接检索者
8. `task_context` 由主脑检索、裁剪后再交给任务系统
9. 每轮反思负责把蒸馏后的价值写入长期 `memory`
10. 周期深反思负责用户整体评估、主脑稳定修正、技能候选沉淀
11. 技能最终进入 `skills`，长期 `memory` 只保留 `skill_hint`

