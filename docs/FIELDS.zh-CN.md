# emoticorebot 字段规范

本文档定义当前架构下的字段与结构约束。

边界、职责、流程请看 `ARCHITECTURE.zh-CN.md`；字段语义与结构以本文为准。

## 1. 适用范围

当前只覆盖：

- `main_brain` 运行时字段
- `executor` 运行时字段
- `light_insight`
- `deep_insight`
- `cognitive_event`
- 长期 `memory` 记录

当前不展开：

- `vision`
- `voice`
- 其他未来感知输入

## 2. 总体约束

1. 系统只有一个主体：`main_brain`
2. `executor` 是执行系统，不是第二主体
3. 运行时事实与主脑解释必须分开存放
4. 每轮必须有一次 `light_insight`
5. `deep_insight` 由 `main_brain` 按需追加，并可被周期信号唤起
6. 长期 `memory` 只接收 `deep_insight` 之后的稳定结论

## 3. 运行时状态字段

### 3.1 `main_brain`

建议结构：

```json
{
  "emotion": "平静",
  "pad": {
    "pleasure": 0.0,
    "arousal": 0.5,
    "dominance": 0.5
  },
  "intent": "",
  "working_hypothesis": "",
  "question_to_executor": "",
  "execution_action": "",
  "execution_reason": "",
  "final_decision": "",
  "final_message": ""
}
```


| 字段 | 含义 |
| --- | --- |
| `emotion` | 当前主脑情绪标签 |
| `pad` | 当前主脑 `PAD` 状态 |
| `intent` | 本轮对用户意图的理解 |
| `working_hypothesis` | 当前工作性判断 |
| `question_to_executor` | 发给 `executor` 的内部问题 |
| `execution_action` | 主脑对执行系统采取的控制动作 |
| `execution_reason` | 做出该控制动作的原因 |
| `final_decision` | `answer / ask_user / continue` |
| `final_message` | 最终对外回复 |

### 3.2 `executor`

建议结构：

```json
{
  "request": "",
  "thread_id": "",
  "run_id": "",
  "control_state": "idle",
  "status": "none",
  "analysis": "",
  "risks": [],
  "recommended_action": "",
  "confidence": 0.0,
  "missing": [],
  "pending_review": {}
}
```


| 字段 | 含义 |
| --- | --- |
| `request` | 主脑交付的内部执行问题 |
| `thread_id` | 当前执行线程 ID |
| `run_id` | 当前执行轮次 ID |
| `control_state` | `idle / running / paused / stopped / completed` |
| `status` | `none / done / need_more / failed` |
| `analysis` | 执行系统的紧凑结论 |
| `risks` | 风险与不确定点 |
| `recommended_action` | 建议主脑选择 `answer / ask_user / continue` |
| `confidence` | 当前结论置信度 |
| `missing` | 当前缺失信息 |
| `pending_review` | 审批 / 编辑 / 恢复所需的信息 |

## 4. 反思字段

### 4.1 `light_insight`

定位：每轮必做的主脑轻反思。

建议结构：

```json
{
  "summary": "",
  "relation_shift": "stable",
  "context_update": "",
  "next_hint": "",
  "execution_review": {
    "summary": "",
    "effectiveness": "none",
    "failure_reason": "",
    "missing_inputs": [],
    "next_execution_hint": ""
  },
  "direct_updates": {
    "user_profile": [],
    "soul_preferences": [],
    "current_state_updates": {
      "pad": null,
      "drives": null
    },
    "applied": {
      "user": false,
      "soul": false,
      "state": false
    },
    "applied_state_snapshot": {}
  }
}
```


| 字段 | 含义 |
| --- | --- |
| `summary` | 本轮即时洞察 |
| `relation_shift` | 关系变化 |
| `context_update` | 上下文变化 |
| `next_hint` | 下一轮主脑如何承接 |
| `execution_review` | 主脑对本轮执行的评价 |
| `direct_updates` | 允许快速更新的候选内容 |

#### `light_insight.execution_review`


| 字段 | 含义 | 边界 |
| --- | --- | --- |
| `summary` | 主脑对本轮执行路径的简要评价 | 不是原始执行日志 |
| `effectiveness` | `high / medium / low / none` | 不是执行状态码 |
| `failure_reason` | 阻塞或失败的主因标签 | 只保留主脑确认的主因 |
| `missing_inputs` | 当前继续执行所缺信息 | 面向恢复，不是全部上下文 |
| `next_execution_hint` | 如果继续执行，下一步怎么走 | 面向执行延续，不是对用户话术 |

#### `light_insight.direct_updates`


| 字段 | 含义 |
| --- | --- |
| `user_profile` | 本轮可快速吸收的用户稳定事实或偏好 |
| `soul_preferences` | 本轮可快速吸收的主脑风格要求 |
| `current_state_updates.pad` | 对 `PAD` 的小幅更新 |
| `current_state_updates.drives` | 对 `social / energy` 的小幅更新 |
| `applied` | 哪些更新已经真正落盘 |
| `applied_state_snapshot` | 更新后状态快照 |

### 4.2 `deep_insight`

定位：由 `main_brain` 按需追加，并可被周期信号唤起的深反思。

建议结构：

```json
{
  "summary": "",
  "self_memories": [],
  "relation_memories": [],
  "insight_memories": [],
  "durable_execution_patterns": [],
  "skill_candidates": []
}
```


| 字段 | 含义 |
| --- | --- |
| `summary` | 一个阶段的高层总结 |
| `self_memories` | 主脑长期自我风格与修正 |
| `relation_memories` | 用户偏好与关系阶段变化 |
| `insight_memories` | 高层认知洞察 |
| `durable_execution_patterns` | 稳定执行模式、常见阻塞与有效路径 |
| `skill_candidates` | 值得升级为 `skills` 的执行模式 |

## 5. `cognitive_event`

定位：主脑视角下的一轮结构化认知切片。

建议结构：

```json
{
  "id": "evt_xxx",
  "version": "2",
  "timestamp": "2026-03-09T10:30:00+08:00",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "actor": "user|assistant",
  "event_type": "user_input|assistant_output",
  "content": "文本内容",
  "state": {},
  "execution": {},
  "light_insight": {},
  "meta": {}
}
```

### 5.1 基础字段


| 字段 | 含义 |
| --- | --- |
| `id` | 事件 ID |
| `version` | 结构版本 |
| `timestamp` | 时间戳 |
| `session_id` | 会话 ID |
| `turn_id` | 轮次 ID |
| `actor` | 事件来源 |
| `event_type` | 事件类型 |
| `content` | 文本内容 |

### 5.2 `state`

`state` 是主脑当前轮的认知状态切片。

```json
{
  "self_state": {
    "pad": {
      "pleasure": 0.12,
      "arousal": 0.58,
      "dominance": 0.44
    },
    "drives": {
      "social": 55,
      "energy": 90
    },
    "mood": "stable",
    "tone": "warm",
    "companionship_tension": 0.62
  },
  "relation_state": {
    "stage": "building_trust",
    "trust": 0.58,
    "familiarity": 0.41,
    "closeness": 0.46
  },
  "context_state": {
    "topic": "architecture",
    "intent": "discussion",
    "recent_focus": [],
    "unfinished_threads": []
  },
  "growth_state": {
    "recent_insights": [],
    "stable_preferences": [],
    "pending_corrections": []
  }
}
```

#### `self_state`


| 字段 | 含义 |
| --- | --- |
| `pad.pleasure` | 愉悦度 |
| `pad.arousal` | 激活度 |
| `pad.dominance` | 主导感 |
| `drives.social` | 社交驱动力 |
| `drives.energy` | 能量驱动力 |
| `mood` | 当前情绪标签 |
| `tone` | 当前主脑语气 |
| `companionship_tension` | 陪伴张力 |

#### `relation_state`


| 字段 | 含义 |
| --- | --- |
| `stage` | 当前关系阶段 |
| `trust` | 信任度 |
| `familiarity` | 熟悉度 |
| `closeness` | 亲近度 |

#### `context_state`


| 字段 | 含义 |
| --- | --- |
| `topic` | 当前主题 |
| `intent` | 当前轮意图判断 |
| `recent_focus` | 最近关注焦点 |
| `unfinished_threads` | 当前未完线索 |

#### `growth_state`


| 字段 | 含义 |
| --- | --- |
| `recent_insights` | 最近洞察 |
| `stable_preferences` | 当前已识别的稳定偏好 |
| `pending_corrections` | 待修正认知 |

### 5.3 `execution`

`execution` 记录本轮执行系统客观上发生了什么。

```json
{
  "invoked": false,
  "control_state": "idle",
  "status": "none",
  "thread_id": "",
  "run_id": "",
  "summary": "",
  "recommended_action": "",
  "confidence": 0.0,
  "missing": [],
  "pending_review": {}
}
```


| 字段 | 含义 |
| --- | --- |
| `invoked` | 本轮是否激活执行系统 |
| `control_state` | 执行控制状态 |
| `status` | 执行结果状态 |
| `thread_id` | 当前执行线程 ID |
| `run_id` | 当前执行轮次 ID |
| `summary` | 执行事实摘要 |
| `recommended_action` | 建议主脑下一步选择 |
| `confidence` | 当前执行结论置信度 |
| `missing` | 当前缺失信息 |
| `pending_review` | 等待审批 / 编辑 / 恢复的信息 |

### 5.4 `light_insight`

`cognitive_event.light_insight` 直接挂载本轮 `light_insight` 结果。

核心边界：

- `execution.summary` 回答“执行做了什么”
- `light_insight.execution_review.summary` 回答“主脑怎么看这次执行”
- 两者相关，但不能互相替代

### 5.5 `meta`

```json
{
  "importance": 0.72,
  "channel": "cli"
}
```


| 字段 | 含义 |
| --- | --- |
| `importance` | 本轮重要性 |
| `channel` | 来源渠道 |

## 6. 长期 `memory` 记录

当前长期记忆主文件：

- `memory/self_memory.jsonl`
- `memory/relation_memory.jsonl`
- `memory/insight_memory.jsonl`

统一建议结构：

```json
{
  "timestamp": "2026-03-10T18:00:00+08:00",
  "type": "self_memory|relation_memory|deep_insight|durable_execution_pattern|skill_candidate",
  "memory": "稳定结论文本",
  "confidence": 0.82,
  "summary": "来自某次深反思的阶段总结",
  "evidence": ["evt_x", "evt_y"],
  "source_event_ids": ["evt_x", "evt_y"]
}
```


| 字段 | 含义 |
| --- | --- |
| `timestamp` | 记忆写入时间 |
| `type` | 记忆类型 |
| `memory` | 稳定结论文本 |
| `confidence` | 置信度 |
| `summary` | 所属阶段总结 |
| `evidence` | 主脑归纳时引用的证据标识 |
| `source_event_ids` | 来源事件 ID |

## 7. 最重要的字段边界

### 7.1 `execution` vs `execution_review`


| 字段 | 回答的问题 | 维护者 |
| --- | --- | --- |
| `execution` | 这次执行客观发生了什么 | 执行结果快照提炼逻辑 |
| `light_insight.execution_review` | 主脑如何评价这次执行 | `main_brain` 轻反思 |

### 7.2 `next_hint` vs `next_execution_hint`


| 字段 | 面向谁 | 含义 |
| --- | --- | --- |
| `light_insight.next_hint` | `main_brain` 下一轮承接 | 下一轮怎么接用户 |
| `light_insight.execution_review.next_execution_hint` | `executor` 继续执行 | 如果继续执行，下一步怎么走 |

### 7.3 运行时材料 vs 长期记忆

- `thread_id / run_id / pending_review / raw trace` 属于运行时材料
- 它们可以进入 `session`、`internal`、`checkpointer`
- 但不能直接进入长期 `memory`
- 长期 `memory` 只保存被 `deep_insight` 确认过的稳定结论
