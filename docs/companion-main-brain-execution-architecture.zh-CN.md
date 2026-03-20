# 陪伴机器人目标架构：单主脑、前台实时 loop、后台执行 loop、SessionWorldState（草案）

## 1. 核心结论

这套目标架构不再把系统理解成两个并列的“脑”。

更准确的定义是：

- 只有一个统一主体，也只有一个真正拥有任务语义和最终表达权的 `main brain`
- 为了保留实时对话能力，`main brain` 拆成两个运行通道：
  - 前台实时 loop
  - 后台执行 loop
- `skills`、工具、重任务执行都属于 `main brain` 控制下的能力层，而不是第二个认知主体

一句话总结：

`main brain` 负责“我怎么理解、怎么决定、怎么说”；后台执行 loop 负责“我怎么做”。

---

## 1.1 正式术语

为避免目标版文档内部术语漂移，这份文档统一使用下列正式名：

- `MainBrainFrontLoop`
  - 中文别名：主脑前台 loop
- `ExecutionRuntime`
  - 中文别名：后台执行 loop
- `SessionWorldState`
  - 中文别名：会话世界状态 / 当前局面板
- `StructuredProgressUpdate`
  - 中文别名：结构化进展更新

如果这份架构文档中的字段例子和契约文档冲突，以：

- [docs/companion-main-brain-execution-module-contracts.zh-CN.md](docs/companion-main-brain-execution-module-contracts.zh-CN.md)

中的正式定义为准。

---

## 2. 为什么要从“左右脑”继续收敛

当前版本的 `left/right brain` 设计已经解决了前台表达与后台执行分离的问题，但随着系统向下面这些能力演进：

- 实时语音
- 流式打断（barge-in）
- 看图后执行任务
- 听语音后安排任务
- 视频/附件驱动 skill 调用
- 多任务并行推进

问题不再是“要不要两个脑”，而是：

- 如何既保留实时对话能力，又允许后台重任务继续推进
- 如何让后台进展对用户可见，但不把工具日志直接裸露给用户
- 如何让多任务共存，但始终只有一个前台焦点

因此新的拆分原则是：

- 不是 `两套脑`
- 而是 `一个主脑 + 两个时序通道 + 一个共享状态面`

---

## 3. 核心组件

### 3.1 `MainBrainFrontLoop`

前台实时 loop，职责：

- 接用户输入
- 处理流式语音 / 文本 / 多模态 turn
- 做快速理解与短响应
- 处理打断、接话、澄清、确认
- 维持人格、情绪与陪伴感
- 决定是否触发后台执行 loop
- 读取后台结果并形成最终用户可见表达

强约束：

- 必须轻量
- 不能被重任务阻塞
- 最终表达权只能在这里

### 3.2 `ExecutionRuntime`

后台执行 loop，职责：

- 接收 `main brain` 触发的后台任务
- 调用 `skills`、工具与能力单元
- 推进长任务、重任务、多步执行
- 收集结构化执行结果
- 将执行状态和阶段性结果写回 `SessionWorldState`

强约束：

- 不直接面向用户说话
- 不拥有任务主权
- 不直接写长期记忆
- 只返回结构化结果、进展、阻塞、产物

### 3.3 `SessionWorldState`

`SessionWorldState` 是前后台共享的当前局面板。

它不是长期记忆，也不是原始聊天历史，而是“当前会话此刻已经确定了什么、做到哪了、接下来该如何收束”的状态面。

### 3.4 `Skills / Capability Layer`

能力层不再被视为“第二个脑”，而是 `main brain` 的可调用能力。

推荐实现原则：

- 优先采用 `一个主脑 agent + skills`
- skill 是边界清晰的能力单元
- 复杂任务只在必要时才走更重的执行路径

典型 skill：

- `transcribe_voice`
- `extract_error_from_image`
- `summarize_video`
- `search_existing_issue`
- `run_command_and_capture_logs`
- `create_calendar_event`

### 3.5 `Memory / Reflection`

- 当前正式存储面：
  - `session/<session_id>/left.jsonl`
    - 对话历史
    - 记录 `用户 <-> 主脑前台` 的原始对话
  - `session/<session_id>/right.jsonl`
    - 内部执行历史
    - 记录 `主脑 <-> 后台执行` 的内部运行记录
  - `memory/cognitive_events.jsonl`
    - 短期记忆
    - 记录浅反思 / 深反思产出的近期认知事件
  - `memory/memory.jsonl`
    - 长期记忆
    - 记录经过治理后沉淀的稳定事实、关系和经验
  - `memory/vector/`
    - 向量镜像
    - 作为检索加速层，不是事实源
- `Memory` 负责长期事实、关系、经验沉淀
- `Reflection` 负责从会话与执行经验中提炼可写入长期记忆的稳定结论
- `turn reflection` / 浅反思
  - 默认按轮运行
  - 主要产物是 `turn_reflection` 与 `cognitive_event`
  - 默认先写入 `memory/cognitive_events.jsonl`
- `deep reflection` / 深反思
  - 默认批量读取近期 `cognitive_events`
  - 负责把稳定结论沉淀到 `memory/memory.jsonl`
  - 负责产出可结晶的 `execution` / `skill_hint` 类长期经验
- `cognitive_events` 属于短期记忆，不是历史日志
- `session/*.jsonl` 属于历史层，不属于记忆层
- `user_updates` / `soul_updates` 是治理输入，不是独立记忆文件
- `memory/vector/` 只镜像已提交的长期记忆，不镜像历史层与全部短期噪声
- 后台执行 loop 产生的原始日志、OCR 噪声、工具轨迹，默认不直接进入长期记忆

---

## 4. 目标运行链路

```text
Channel / Stream Input
  -> InputNormalizer
  -> MainBrainFrontLoop
     -> 如需即时响应：stream / inline 短回复
     -> 如需后台能力：触发 committed turn / task trigger
  -> ExecutionRuntime
     -> 调用 skills / tools
     -> 写回结构化结果到 SessionWorldState
  -> MainBrainFrontLoop
     -> 读取 SessionWorldState
     -> 决定是否继续、追问、确认、push 或最终收束
  -> Output / Safety / Delivery
```

关键原则：

- 前台负责“先接住用户”
- 后台负责“帮用户做事”
- 用户只面对同一个主体

---

## 5. 用户如何看到后台任务细节

后台执行 loop 的进展不是不能对用户可见，而是：

- 执行层只产出结构化进展
- 这些进展先写入 `SessionWorldState`
- 再由 `main brain` 决定哪些内容可见、以什么口吻可见、何时可见

正确链路是：

```text
ExecutionRuntime
  -> StructuredProgressUpdate
  -> SessionWorldState
  -> MainBrainFrontLoop
  -> User-visible update
```

而不是：

```text
ExecutionRuntime -> 直接对用户说话
```

任务细节应分为两类：

- 机器细节
  - 原始日志
  - OCR/ASR 原始输出
  - 工具 trace
  - 中间 JSON
- 用户可见进展
  - 已开始处理
  - 已识别出关键信息
  - 正在查资料 / 正在执行
  - 发现了关键结果 / 遇到需要确认的问题

默认只把第二类转译给用户。

---

## 6. 多任务与前台焦点

系统必须允许多个任务共存，但任何时刻只能有一个前台主线。

因此 `SessionWorldState` 里不能只有一个 `active_task`，而应维护：

- `foreground_task_id`
- `background_task_ids`
- `tasks`

### 6.1 设计原则

- 执行层可以并行推进多个任务
- 但只有 `main brain` 决定当前前台关注哪个任务
- 后台任务是否打断用户，必须受可见性与中断策略控制

### 6.2 任务状态至少要有

- `pending`
- `running`
- `waiting_user`
- `scheduled`
- `done`
- `failed`
- `cancelled`

### 6.3 每个任务建议带的可见性字段

- `visibility`
  - `silent`
  - `concise`
  - `verbose`
- `interruptibility`
  - `never`
  - `important_only`
  - `always`

这两个字段用于控制：

- 是否主动告诉用户
- 是否允许打断当前对话

---

## 7. `SessionWorldState` 的定位

### 7.1 它是什么

它是：

- 当前意图
- 当前对话阶段
- 当前用户状态
- 当前前后台任务状态
- 最近一次感知与执行结果
- 当前回复策略

### 7.2 它不是什么

它不是：

- 长期记忆
- 原始会话日志
- 原始工具 trace
- 事实真相的唯一历史档案

### 7.3 聊天场景例子

用户说：`今天有点累，顺便提醒我晚上九点洗衣服。`

此时 `SessionWorldState` 可以写成：

```json
{
  "session_id": "sess_001",
  "conversation_phase": "multitask_chat",
  "foreground_task_id": null,
  "background_task_ids": ["task_reminder"],
  "user_state": {
    "emotion": "tired",
    "energy": "low",
    "confidence": 0.84
  },
  "active_topics": ["今天很累", "晚上九点洗衣服提醒"],
  "confirmed_facts": {
    "today_feels_tired": true,
    "reminder_time": "21:00",
    "reminder_topic": "洗衣服"
  },
  "open_questions": [],
  "tasks": {
    "task_reminder": {
      "task_id": "task_reminder",
      "title": "创建洗衣服提醒",
      "kind": "reminder",
      "status": "scheduled",
      "priority": 35,
      "visibility": "silent",
      "interruptibility": "never",
      "user_visible": false,
      "goal": "在今晚 21:00 提醒用户洗衣服",
      "current_chunk": null,
      "recent_observations": ["提醒参数已解析"],
      "artifacts": [],
      "last_user_visible_update": "提醒待创建",
      "waiting_for_user": false,
      "risk_flags": []
    }
  },
  "perception_summary": {
    "images": [],
    "audio": [],
    "video": [],
    "files": []
  },
  "reply_strategy": {
    "goal": "先接住用户疲惫感，再说明提醒会一起处理",
    "style": "温和、简短、陪伴式",
    "delivery_mode": "inline",
    "needs_tool": true
  },
  "risk_flags": []
}
```

这个例子的重点是：

- `main brain` 看到用户是疲惫状态
- 提醒任务已存在，但此刻不应抢占前台焦点
- 回复要先共情，再顺带承诺会处理提醒

这个 JSON 例子中的字段定义以契约文档为准，尤其是：

- `conversation_phase`
- `foreground_task_id`
- `background_task_ids`
- `SessionTaskState.visibility`
- `SessionTaskState.interruptibility`
- `reply_strategy.delivery_mode`

---

## 8. 记忆分层如何变化

目标版架构下，要先把历史层和记忆层分开，再谈记忆内部的分层。

### 8.1 历史层

历史层只负责保留原始过程，不直接等于记忆：

- `session/<session_id>/left.jsonl`
  - 对话历史
  - 记录用户与主脑前台之间的原始往来
- `session/<session_id>/right.jsonl`
  - 内部执行历史
  - 记录主脑触发后台执行后的内部运行记录

强约束：

- 这两份都是历史，不是长期记忆
- 也不应被误叫成短期记忆

### 8.2 短期记忆

短期记忆当前正式定义为：

- `memory/cognitive_events.jsonl`

它存：

- 近期认知事件
- 浅反思结果
- 深反思过程里产生、但尚未固化成长记忆的结论
- 对当前几轮决策仍然有价值的临近经验

默认来源：

- `turn reflection`
- 反思治理阶段回写的阶段性认知材料

强约束：

- `cognitive_events` 是短期记忆
- 不是 `session` 历史
- 不是 `memory/short_term/` 目录语义
- 浅反思里出现的 `memory_candidates`，在目标版语义上首先是候选，不默认等于已经固化的长期记忆

### 8.3 长期记忆

存稳定内容：

- 用户事实
- 关系结论
- 人格沉淀
- 经过治理后的执行经验

当前正式路径：

- `memory/memory.jsonl`

写入路径：

- `deep reflection`
- 显式治理通过后的 `reflection write`
- `main brain` 仅在少量明确治理过的直写场景下参与

执行 loop 默认不直接写这一层。

### 8.4 `SessionWorldState`

存当前局面：

- 当前意图
- 当前任务
- 当前多模态观察
- 最近 skill 结果
- 当前回复策略

这是工作态，不是长期事实源。

### 8.5 向量镜像

向量镜像当前正式路径：

- `memory/vector/`

它负责：

- 为长期记忆提供检索加速
- 为召回层提供向量索引

强约束：

- 它不是事实源
- 它不替代 `memory/memory.jsonl`
- 它只镜像已经提交的长期记忆

### 8.6 执行中间态

执行 loop 运行时可能还需要内部 scratchpad：

- 中间日志
- 原始 OCR / ASR
- 临时产物
- 调用参数
- 失败原因

这一层可以存在，但默认不直接暴露给用户，也不直接写入长期记忆。

---

## 9. 能力层边界

推荐原则：

- 只有一个 `main brain`
- 能力层不再被叫作“第二个脑”
- 能力层本质上是主脑控制下的 skills / tools / routines

### 9.1 `main brain` 负责

- 是否调用某个 skill
- 先调用哪个 skill
- 是否并行
- 结果是否足够
- 是否需要追问或确认
- 最终怎么对用户说

### 9.2 skill / 能力层负责

- 在明确边界内执行一次能力调用
- 返回结构化结果
- 不自己改变任务目标
- 不直接对用户发言

### 9.3 为什么默认不把能力层做成一堆重 worker

原因是：

- 太重
- 边界容易漂移
- 实时系统成本高
- 容易出现第二个“像脑一样”的执行体

因此默认建议是：

- `一个主脑 agent + 一组 skills`
- 只有复杂场景才升级到更重的执行模式

---

## 10. 对当前代码的映射

### 10.1 目标代码目录结构

```text
emoticorebot/
├── main_brain/
│   ├── runtime.py
│   ├── context.py
│   ├── planner.py
│   ├── reply_policy.py
│   └── perception.py
├── execution/
│   ├── runtime.py
│   ├── executor.py
│   ├── chunking.py
│   ├── progress.py
│   └── store.py
├── session/
│   ├── runtime.py
│   ├── models.py
│   └── thread_store.py
├── memory/
│   ├── store.py
│   ├── retrieval.py
│   ├── vector_index.py
│   └── crystallizer.py
├── reflection/
│   ├── runtime.py
│   ├── governor.py
│   ├── manager.py
│   ├── turn.py
│   ├── deep.py
│   └── cognitive.py
├── skills/
├── tools/
├── protocol/
├── input/
├── output/
├── delivery/
└── safety/
```

这棵树表达的是目标版职责收敛：

- `main_brain/`
  - 统一承载主脑理解、规划、回复收束
- `execution/`
  - 统一承载 chunk 执行、进展回写、执行产物管理
- `session/`
  - 统一承载 `SessionWorldState` 与历史索引
- `memory/`
  - 统一承载正式记忆读写、检索、向量镜像、技能结晶
- `reflection/`
  - 统一承载浅反思、深反思、认知事件生成、治理

### 10.2 目标运行数据目录结构

```text
workspace/
├── memory/
│   ├── cognitive_events.jsonl
│   ├── memory.jsonl
│   └── vector/
├── session/
│   └── <session_id>/
│       ├── left.jsonl
│       └── right.jsonl
└── skills/
    └── <skill_name>/
        └── SKILL.md
```

### 10.3 目标模块落位

- `emoticorebot/main_brain/runtime.py`
  - 主脑前台实时 loop
  - 负责理解、规划、回复收束与执行触发

- `emoticorebot/main_brain/context.py`
  - 主脑上下文拼装
  - 汇聚 `SessionWorldState`、短期记忆、长期记忆与感知摘要

- `emoticorebot/main_brain/reply_policy.py`
  - 用户可见回复策略
  - 负责 `inline / push / stream` 收束

- `emoticorebot/execution/runtime.py`
  - 后台执行 loop 运行时
  - 负责 chunk 生命周期与进展回写

- `emoticorebot/execution/executor.py`
  - 后台执行内核
  - 负责 skill / tool 调用与单次 chunk 收束

- `emoticorebot/session/runtime.py`
  - `SessionWorldState` 持有与同步
  - 会话级历史索引与任务状态管理

- `emoticorebot/session/models.py`
  - `SessionWorldState`、`SessionTaskState` 等正式模型

- `emoticorebot/memory/retrieval.py`
  - 正式长期记忆读侧
  - 默认主要服务 `main_brain`

- `emoticorebot/reflection/governor.py`
  - 反思治理入口
  - 负责短期沉淀、长期提交与技能结晶触发

---

## 11. 文档独立性

这份文档应独立成立。

它定义的是目标版正式架构，不依赖其他旧版左右脑文档。

---

## 12. 一句话版本

目标版架构的一句话是：

`同一个 main brain 通过前台实时 loop 与后台执行 loop 协同工作，依靠 SessionWorldState 保持对话、任务、多模态观察和用户可见进展的一致性。`
