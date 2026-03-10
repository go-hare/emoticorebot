# emoticorebot 字段规范

本文档定义当前目标架构下的字段结构、字段语义与记录边界。

边界、职责、流程请看 `ARCHITECTURE.zh-CN.md`；字段语义、准入规则与存储边界以本文为准。

---

## 1. 适用范围

当前覆盖：

- `session` 原始记录
- `main_brain` 运行时字段
- `executor` 运行时字段
- `executor_context` 执行上下文包
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

## 2. 兼容命名

为便于迁移，本文使用以下兼容命名：

- `turn_reflection` == 旧命名 `light_insight`
- `deep_reflection` == 旧命名 `deep_insight`

说明：

1. `turn_reflection` 与 `deep_reflection` 是反思机制，不是独立长期存储层
2. 长期沉淀统一进入一个长期 `memory` 存储
3. `deep_reflection` 可由按需触发或周期信号触发

---

## 3. 总体约束

1. 系统只有一个主体：`main_brain`
2. `executor` 是执行系统，不是第二主体
3. `session` 只保存原始运行时材料，不保存长期解释结论
4. 长期记忆只有一个统一存储：`memory`
5. 长期 `memory` 的人类可读源为 `memory.jsonl`
6. 向量库是 `memory.jsonl` 的检索镜像，不是语义源头
7. `main_brain` 是唯一检索者
8. `executor` 不直接检索长期 `memory`
9. `executor` 所需的执行经验、工具记忆、`skill_hint` 由 `main_brain` 检索并打包后传入
10. 每轮结束都应至少产生一次 `turn_reflection`
11. `turn_reflection` 可把高置信用户信息、主脑风格修正和小幅 `state_update` 直接写回托管锚点或状态文件
12. `deep_reflection` 负责阶段性归纳、用户整体评估、技能候选沉淀
13. 长期 `memory` 只保存蒸馏后的稳定价值，不保存原始大段对话或大段工具输出
14. 已稳定沉淀到 `skills` 的能力，不重复把完整技能内容写入 `memory`，只保存 `skill_hint`

---

## 4. 分层原则

### 4.1 分层总表

| 层级 | 代表对象 | 生命周期 | 保存内容 | 不保存内容 |
| --- | --- | --- | --- | --- |
| 原始层 | `session` / `executor_trace` | 当前轮到若干轮 | 原始对话、原始工具调用、暂停恢复现场 | 长期解释结论 |
| 认知层 | `cognitive_event` | 持续累积 | 主脑视角下的一轮结构化认知切片 | 大量原始工具日志 |
| 反思层 | `turn_reflection` / `deep_reflection` | 每轮或按需/周期产生 | 本轮解释、阶段归纳、候选长期结论 | 最终长期存储真身 |
| 长期层 | `memory` | 长期 | 已蒸馏的稳定事实、经验、模式、提示 | 原始日志、`thread_id`、`run_id`、完整技能正文 |
| 索引层 | `memory/chroma/` | 长期 | 检索辅助字段、向量、访问统计 | 人类可读语义源 |

### 4.2 基本规则

1. 原始层与长期层必须分离
2. 反思层可以产出候选长期记忆，但不等于长期记忆本身
3. 长期更新采用追加式写入；若修正旧结论，应新增记录并通过 `links.supersedes` 或 `links.invalidates` 建链
4. 一条长期记忆只表达一个清晰语义单元，不混写多个不相干结论

---

## 5. `session` 原始记录

定位：运行时原始材料，供回放、恢复、审计与短期上下文使用。

建议结构：

```json
{
  "id": "sess_evt_xxx",
  "timestamp": "2026-03-10T20:10:00+08:00",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "actor": "user",
  "event_type": "message",
  "content": "帮我看看这个错误",
  "raw_payload": {},
  "meta": {}
}
```

### 5.1 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 原始事件 ID |
| `timestamp` | 时间戳 |
| `session_id` | 会话 ID |
| `turn_id` | 当前轮次 ID |
| `actor` | `user / assistant / system / tool` |
| `event_type` | `message / tool_call / tool_result / control / system_note` |
| `content` | 原始文本或简要原始内容 |
| `raw_payload` | 原始结构化载荷 |
| `meta` | 附加元数据 |

### 5.2 约束

1. `session` 是原始层，不写 `importance / confidence / stability`
2. `session` 可存原始工具大输出，但长期 `memory` 不应直接复制
3. `session` 可用于恢复执行现场，但不应被当作长期认知结论

---

## 6. 运行时状态字段

### 6.1 `main_brain`

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
  "execution_request": "",
  "execution_action": "none",
  "execution_reason": "",
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
| `retrieval_focus` | 检索关注点，如 `user / tool / relationship / skill` |
| `retrieved_memory_ids` | 本轮实际命中的长期记忆 ID |
| `execution_request` | 发给 `executor` 的内部请求 |
| `execution_action` | 对 `executor` 的控制动作 |
| `execution_reason` | 采取该控制动作的原因 |
| `final_decision` | `answer / ask_user / continue_executor / pause_executor / stop_executor` |
| `final_message` | 最终对外回复 |

### 6.2 `executor`

定位：执行系统的当前轮运行时状态。

建议结构：

```json
{
  "request": "",
  "thread_id": "",
  "run_id": "",
  "control_state": "idle",
  "status": "none",
  "analysis": "",
  "final_result": "",
  "risks": [],
  "missing": [],
  "pending_review": {},
  "recommended_action": "answer",
  "confidence": 0.0
}
```

| 字段 | 含义 |
| --- | --- |
| `request` | 主脑交付的内部执行问题 |
| `thread_id` | 当前执行线程 ID |
| `run_id` | 当前执行轮次 ID |
| `control_state` | `idle / running / paused / stopped / completed` |
| `status` | `none / done / need_more / failed` |
| `analysis` | 执行系统的紧凑分析结论 |
| `final_result` | 面向主脑的最终执行结果 |
| `risks` | 风险、不确定点、边界提醒 |
| `missing` | 继续执行所缺少的信息 |
| `pending_review` | 等待审批、编辑、确认的动作信息 |
| `recommended_action` | 建议主脑执行的下一控制动作 |
| `confidence` | 当前结论置信度 |

---

## 7. `executor_context`

定位：`main_brain` 检索并裁剪后，传给 `executor` 的执行上下文包。

建议结构：

```json
{
  "goal": "",
  "request": "",
  "constraints": [],
  "relevant_execution_memories": [],
  "relevant_tool_memories": [],
  "skill_hints": [],
  "success_criteria": [],
  "return_contract": {
    "mode": "final_only",
    "must_not": [
      "direct_user_reply",
      "memory_retrieval",
      "memory_write"
    ]
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `goal` | 当前执行目标 |
| `request` | 主脑给执行系统的内部问题 |
| `constraints` | 用户约束、环境约束、权限约束 |
| `relevant_execution_memories` | 与当前任务相关的执行经验记忆 |
| `relevant_tool_memories` | 与当前工具相关的工具经验记忆 |
| `skill_hints` | 已沉淀技能的触发提示 |
| `success_criteria` | 成功标准 |
| `return_contract` | 返回约束与边界说明 |

---

## 8. `cognitive_event`

定位：主脑视角下的一轮结构化认知切片。

建议结构：

```json
{
  "id": "evt_xxx",
  "version": "3",
  "timestamp": "2026-03-10T20:10:00+08:00",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "user_input": "用户原始输入",
  "main_brain_state": {},
  "retrieval": {
    "query": "",
    "memory_ids": []
  },
  "executor": {
    "used": false,
    "status": "none",
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
| `main_brain_state` | 主脑状态切片 |
| `retrieval.query` | 主脑检索查询 |
| `retrieval.memory_ids` | 本轮命中的长期记忆 ID |
| `executor.used` | 是否调用了执行系统 |
| `executor.status` | 执行状态摘要 |
| `executor.summary` | 执行摘要 |
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
  "execution_review": {
    "attempt_count": 0,
    "effectiveness": "medium",
    "main_failure_reason": "",
    "missing_inputs": [],
    "next_execution_hint": ""
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `summary` | 本轮发生了什么的简要洞察 |
| `problems` | 本轮暴露的问题列表 |
| `resolution` | 问题最终如何被解决 |
| `outcome` | `success / partial / failed / no_execution` |
| `next_hint` | 下一轮主脑如何承接 |
| `user_updates` | 本轮可直接写入 `USER.md` 托管锚点块的高置信用户信息候选 |
| `soul_updates` | 本轮可直接写入 `SOUL.md` 托管锚点块的高置信主脑风格候选 |
| `state_update` | 对 `current_state.md` 的小幅增量更新建议 |
| `memory_candidates` | 本轮拟写入长期 `memory` 的候选记录 |
| `execution_review` | 对本轮执行过程的评价 |

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
| `skill_hints` | 值得沉淀为 `skill_hint` 的执行提示候选 |

---

## 10. 统一长期 `memory`

定位：统一长期记忆层。

存储模型：

- `memory.jsonl`：人类可读、可审计、追加式写入的源存储
- `memory/chroma/`：面向检索的 Chroma 镜像层
- 两者通过同一个 `memory.id` 对齐

### 10.1 记录结构

建议结构：

```json
{
  "schema_version": "memory.v1",
  "id": "mem_xxx",
  "created_at": "2026-03-10T20:10:00+08:00",
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
| `main_brain` | 主要供主脑理解用户、关系、自我与长期判断使用 |
| `executor` | 主要供执行系统消费，但仍由主脑检索后转交 |
| `shared` | 可同时支持主脑与执行，但仍由主脑统一检索与裁剪 |

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
| `user_fact` | `main_brain` | `durable` | 用户稳定事实 |
| `preference` | `main_brain` | `durable` | 用户偏好、厌恶、风格倾向 |
| `goal` | `shared` | `durable` 或 `episodic` | 用户目标或当前任务 |
| `constraint` | `shared` | `durable` 或 `episodic` | 用户边界、限制、禁忌、环境约束 |
| `relationship` | `main_brain` | `durable` | 用户与人、事、物的关系状态 |
| `soul_trait` | `main_brain` | `durable` | 主脑长期风格、自我修正与人格锚点 |

#### 执行与经验相关

| `type` | 推荐 `audience` | 推荐 `kind` | 含义 |
| --- | --- | --- | --- |
| `turn_insight` | `shared` | `episodic` | 当前轮发生了什么、问题是什么、怎么解决的 |
| `tool_experience` | `executor` | `procedural` | 某工具在某类任务上的执行经验 |
| `error_pattern` | `executor` | `procedural` | 错误特征到解决方案的模式 |
| `workflow_pattern` | `executor` 或 `shared` | `procedural` | 多工具组合形成的稳定路径 |
| `skill_hint` | `executor` | `procedural` | 已沉淀技能的触发提示，而不是技能全文 |

### 10.7 `source`

建议结构：

```json
{
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "event_ids": ["evt_a", "evt_b"],
  "producer": "main_brain.turn_reflection",
  "tool_names": ["shell", "web"],
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
  "attempt_count": 0,
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
  "constraint": "executor 不直接检索 memory",
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
  "trait": "对架构边界敏感",
  "direction": "strengthen",
  "basis": "多轮讨论后用户持续强调主体边界",
  "evidence_count": 6
}
```

#### H. `tool_experience`

```json
{
  "tool_name": "shell",
  "task_signature": "读取并比对文档字段",
  "failure_mode": "无",
  "resolution": "直接读取本地文件并结构化整理",
  "success": true,
  "latency_hint": "low",
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
  "goal_cluster": "字段规范整理",
  "tool_sequence": ["shell", "shell", "analysis"],
  "preconditions": ["本地仓库可读"],
  "steps_summary": "先读现有文档，再抽字段，再输出结构化草案",
  "sample_size": 5,
  "success_rate": 0.8
}
```

#### K. `skill_hint`

```json
{
  "skill_id": "skill_memory_schema",
  "skill_name": "字段规范整理",
  "trigger": "当任务要求输出结构化字段文档时",
  "hint": "优先抽出公共字段、枚举和 payload，再补示例",
  "applies_to_tools": ["shell", "analysis"]
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
    "last_retrieved_at": "2026-03-11T10:10:00+08:00",
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
3. `executor` 不直接查询索引层
4. 当前实现采用词法分数 + 向量分数的混合排序
5. 访问统计优先放在索引 sidecar，而不是 `memory.jsonl`
6. `last_relevance_score` 是检索统计，不等于长期事实语义

---

## 12. 示例

### 12.1 `turn_insight`

```json
{
  "schema_version": "memory.v1",
  "id": "mem_01",
  "created_at": "2026-03-10T20:10:00+08:00",
  "audience": "shared",
  "kind": "episodic",
  "type": "turn_insight",
  "summary": "多次工具尝试后最终成功，关键阻塞是权限限制",
  "content": "本轮为了完成任务进行了多次工具尝试。中途因权限限制导致执行受阻，最终改用替代路径成功完成。后续类似情况应优先检查权限边界并准备降级方案。",
  "importance": 7,
  "confidence": 0.92,
  "stability": 0.56,
  "status": "active",
  "tags": ["tool", "retry", "fallback"],
  "source": {
    "session_id": "sess_x",
    "turn_id": "turn_12",
    "event_ids": ["evt_1", "evt_2"],
    "producer": "main_brain.turn_reflection",
    "tool_names": ["shell", "web"]
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
    "problem": "权限限制导致执行受阻",
    "attempt_count": 4,
    "resolution": "改用替代路径",
    "outcome": "success",
    "follow_up": "下次先检查权限与可用降级方案"
  },
  "expires_at": null,
  "metadata": {}
}
```

### 12.2 `tool_experience`

```json
{
  "schema_version": "memory.v1",
  "id": "mem_02",
  "created_at": "2026-03-10T20:12:00+08:00",
  "audience": "executor",
  "kind": "procedural",
  "type": "tool_experience",
  "summary": "读取本地文档并整理字段时，优先直接读现有文档结构",
  "content": "当任务是整理字段规范且本地仓库可读时，先读取现有架构文档和字段文档，再输出统一字段表，通常比直接从零起草更稳定。",
  "importance": 8,
  "confidence": 0.88,
  "stability": 0.73,
  "status": "active",
  "tags": ["doc", "schema", "local-read"],
  "source": {
    "session_id": "sess_x",
    "turn_id": "turn_12",
    "event_ids": ["evt_3"],
    "producer": "main_brain.turn_reflection",
    "tool_names": ["shell"]
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
    "tool_name": "shell",
    "task_signature": "字段文档整理",
    "failure_mode": "",
    "resolution": "先读取已有文档结构再整理输出",
    "success": true,
    "latency_hint": "low",
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
  "created_at": "2026-03-10T20:20:00+08:00",
  "audience": "executor",
  "kind": "procedural",
  "type": "skill_hint",
  "summary": "遇到字段文档类任务时，优先输出公共字段、枚举、payload 三段式结构",
  "content": "当任务目标是制定结构化字段文档时，优先划分公共字段、枚举语义和按类型扩展字段，再用少量示例校验完整性。",
  "importance": 7,
  "confidence": 0.84,
  "stability": 0.81,
  "status": "active",
  "tags": ["skill", "schema", "writing"],
  "source": {
    "session_id": "sess_x",
    "turn_id": "turn_20",
    "event_ids": ["evt_7", "evt_8"],
    "producer": "main_brain.deep_reflection",
    "tool_names": ["shell", "analysis"]
  },
  "links": {
    "related_ids": [],
    "evidence_ids": ["evt_7", "evt_8"],
    "entity_ids": [],
    "skill_ids": ["skill_memory_schema"],
    "supersedes": [],
    "invalidates": []
  },
  "payload": {
    "skill_id": "skill_memory_schema",
    "skill_name": "字段规范整理",
    "trigger": "当任务要求输出结构化字段文档时",
    "hint": "先列公共字段，再列 type 扩展字段，最后补示例",
    "applies_to_tools": ["shell", "analysis"]
  },
  "expires_at": null,
  "metadata": {}
}
```

---

## 13. 最终边界总结

1. `session` 只存原始数据
2. `memory` 是统一长期存储
3. `main_brain` 是唯一长期记忆检索者
4. `executor` 只接收主脑打包后的执行上下文
5. 每轮反思直接把蒸馏后的价值写入长期 `memory`
6. 周期深反思负责用户整体评估、主脑稳定修正、技能候选沉淀
7. 技能最终进入 `skills`，长期 `memory` 只保留 `skill_hint`
8. 当同一 `skill_hint` 聚合到足够支持度后，可物化为 `workspace/skills/<skill>/SKILL.md`；技能正文不回写进 `memory`
