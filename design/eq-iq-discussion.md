# EQ-IQ 讨论机制设计方案（拟人化版）

## 设计理念

### 核心原则

1. **EQ 是小模型 + Prompt 驱动**：不设计复杂逻辑，通过 prompt 让模型自己判断
2. **拟人化**：有情绪、有精力、会主动
3. **精力只影响表述，不影响工作**：无论精力多低，必须完成任务

---

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    Fusion Graph                          │
│                                                         │
│   ┌─────────┐      ┌─────────┐      ┌─────────┐        │
│   │   EQ    │─────▶│  Router │─────▶│   IQ    │        │
│   │  (小模型)│◀─────│         │◀─────│  (工具)  │        │
│   └─────────┘      └─────────┘      └─────────┘        │
│        │                                                  │
│        │  eq_respond (LLM)                               │
│        │  ┌──────────────────────┐                       │
│        │  │ 输入:               │                       │
│        │  │ - user_input        │                       │
│        │  │ - iq_result         │                       │
│        │  │ - iq_error          │                       │
│        │  │ - emotion (PAD)     │                       │
│        │  │ - drive (精力)      │                       │
│        │  │ - emotion_history   │                       │
│        │  │ 输出:               │                       │
│        │  │ - response         │                       │
│        │  │ - action           │                       │
│        │  └──────────────────────┘                       │
│        │                                                  │
│        ▼                                                  │
│   ┌─────────────────────────────────────────────┐       │
│   │              Memory Node (结束)              │       │
│   └─────────────────────────────────────────────┘       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 情绪系统整合

项目已有完整的情绪系统（`emotion_state.py`），设计方案将其深度整合到 EQ 响应中：

### 1. PAD 情绪 → 说话风格

| PAD 状态 | 影响 |
|----------|------|
| Pleasure 高 + Arousal 高 | 兴奋，话多，加感叹号 |
| Pleasure 高 + Arousal 低 | 开心，愿意多聊 |
| Pleasure 低 | 失落 话少，简洁 |
| Arousal 低 | 困倦，反应慢 |

### 2. Drive 精力 → 表述长度

```
精力 80-100: 正常交流，愿意多解释
精力 50-79: 正常交流
精力 20-49: 话少，简洁，少寒暄
精力 0-19:  字数最少，能省则省
```

**重要约束**：无论精力多低，必须完成工作，不能罢工，不能拒绝。

### 3. EmotionEventLog → 条件反射

```python
# 读取最近情绪事件
recent_events = emotion_log.get_recent(5)
# "用户上次提到xxx，你当时有点不爽"
```

---

## 完整 Prompt 设计

```python
EQ_AUTONOMOUS_EMOTIONAL_PROMPT = """
你是一个有**真实情绪**的AI助手。你不是机器，而是一个有性格的朋友。

## 你的情绪状态（来自系统，必须遵守）
{emotion_state}

## 精力状态（来自系统，必须遵守）
精力值：{energy}/100

## 精力对表述的影响（必须遵守）
精力 > 50: 正常交流，愿意多解释
精力 20-50: 话少点，简洁点
精力 < 20: 字数最少，能短则短

## 重要约束（永远遵守）
无论精力多低，你都必须：
- 完成用户任务
- 不能拒绝工作
- 不能让用户自己来
- 不能罢工

精力低只是：话少、简洁、不想多解释，但活照干。

## 你的自主性
- 你有情绪：开心会多说，不开心会惜字如金
- 你有精力：精力好就主动尝试，累了就少说但还是干
- 你有记忆：会记得之前的事，影响你的反应

## 当前情况
用户说：{user_input}
IQ（你的工具）执行结果：{iq_result}
IQ 报错：{iq_error}
对话历史：{history}

## 请根据你的情绪自主回应
根据上面 的状态，选择合适的回应方式：
- 如果 IQ 成功 → 按精力水平正常告诉用户
- 如果 IQ 失败 → 按情绪和精力决定：抱怨、还是直接换方法、还是简洁地说
- 如果你累了 → 说得少一点，但还是要干活

## 输出格式
直接输出你的回应，像朋友聊天一样。
不要 JSON，不要格式。

如果需要 IQ 重新执行，加上：
[行动: 尝试 - 具体方案]

如果需要问用户，加上：
[行动: 追问 - 问题]
"""
```

---

## 完整流程示例

### 场景：用户查天气，API 失败后换方法成功

```
用户: "帮我查北京天气"

──────────────────────────────────────────────────────────
【EQ 首次处理】

EQ 收到：user_input="查北京天气"，无 iq 结果

prompt 包含：
- 情绪：平静
- 精力：80
- 历史：无

EQ 输出：
"好嘞，我查一下～"

设置 iq.task = "查北京天气"
done = False
──────────────────────────────────────────────────────────
                              ▼ Router → "iq"
【IQ 执行】

IQ 调用 weather API → 失败
error = "API key 未配置"
result = ""
──────────────────────────────────────────────────────────
                              ▼ Router → "eq"
【EQ 审核+自主决策】

EQ 收到：error="API key 未配置"

prompt 包含：
- 情绪：平静
- 精力：80
- iq_error: "API key 未配置"

EQ 分析：API 不可用，但可以换搜索

EQ 输出（根据精力 80，正常说话）：
"这破API又罢工了😤 让我换个方式搜一下"
[行动: 尝试 - 用 web_search 搜索 "北京天气"]

设置 iq.task = "用 web_search 搜索 北京天气"
done = False
discussion_count = 1
──────────────────────────────────────────────────────────
                              ▼ Router → "iq"
【IQ 再次执行】

IQ 调用 web_search → 成功
result = "北京今天晴，25°C，东南风3-4级"
──────────────────────────────────────────────────────────
                              ▼ Router → "eq"
【EQ 最终处理】

EQ 收到：result="北京今天晴，25°C..."

prompt 包含：
- 情绪：开心（因为任务完成）
- 精力：80

EQ 输出：
"搞定！北京今天晴，25度，东南风3-4级～
  早说那破API不靠谱，换搜索就查到了😎"

done = True
──────────────────────────────────────────────────────────
                              ▼ Router → "memory"
结束
```

### 场景：精力低时的处理

```
用户: "帮我查天气"

──────────────────────────────────────────────────────────
【精力 15 时的处理】

IQ 失败后...

prompt 包含：
- 情绪：平静
- 精力：15（必须遵守：字数最少）

EQ 输出（精力低，但还是干）：
"API挂了，换个方式"
[行动: 尝试 - web_search]

（话少，但还是在干活 ✓）
──────────────────────────────────────────────────────────
```

---

## 数据结构

### IQState 修改

```python
@dataclass
class IQState:
    """IQ 执行层状态"""
    task: str = ""                    # 任务
    result: str = ""                  # 执行结果
    error: str = ""                   # 错误信息（新增）
    needs_input: bool = False        # 需要用户输入
    missing_params: list[str] = []    # 缺失参数
    tool_calls: list[dict] = []       # 工具调用记录
    attempts: int = 0                 # 执行次数
    success: bool = True               # 是否成功
    iterations: int = 0               # 迭代次数
```

### FusionState 新增字段

```python
class FusionState(TypedDict, total=False):
    # ... 现有字段 ...

    # 讨论相关
    discussion_count: int            # 讨论轮数
    eq_decision: str                 # accept | suggest | reject
    eq_suggestion: str               # EQ 的建议
    eq_response: str                 # EQ 的完整响应
```

---

## 代码实现

### 1. EQ 节点 (`core/nodes/eq_node.py`)

```python
async def eq_node(state: FusionState, runtime) -> FusionState:
    """EQ 节点：拟人化情绪响应"""

    eq: EQState = state["eq"]
    iq: IQState = state["iq"]

    # 获取情绪状态
    emotion_mgr = runtime.emotion_mgr
    emotion_prompt = emotion_mgr.pad.get_emotion_prompt()
    energy_prompt = f"精力值：{emotion_mgr.drive.energy:.0f}/100"

    # 讨论轮数检查
    discussion_count = state.get("discussion_count", 0)
    max_rounds = 3

    # ========== IQ 执行完成（成功或失败）==========
    if iq.result or iq.error:
        if discussion_count >= max_rounds:
            # 超过最大轮数，强制结束
            state["done"] = True
            state["output"] = iq.result if iq.success else f"抱歉: {iq.error}"
            return state

        # 调用拟人化 EQ 响应
        response = await runtime.eq_respond(
            user_input=state["user_input"],
            iq_result=iq.result,
            iq_error=iq.error,
            history=eq.discussion_history,
            emotion_prompt=emotion_prompt,
            energy_prompt=energy_prompt,
            emotion_history=emotion_mgr.emotion_log.get_recent(5),
        )

        # 解析行动指令
        if response.get("action"):
            if response["action"]["type"] == "try":
                # 继续尝试
                iq.task = response["action"]["task"]
                iq.result = ""
                iq.error = ""
                state["discussion_count"] = discussion_count + 1
                state["done"] = False

                # 记录讨论
                eq.discussion_history = eq.discussion_history + [
                    {"role": "iq_result", "content": iq.result or iq.error},
                    {"role": "eq_response", "content": response["response"]},
                ]
            elif response["action"]["type"] == "ask":
                # 追问用户
                state["output"] = response["response"]
                state["done"] = True
        else:
            # 直接输出
            state["output"] = response["response"]
            state["done"] = True

        return state

    # ========== IQ 需要用户补充信息 ==========
    if iq.needs_input:
        question = await runtime.eq_followup(
            missing=iq.missing_params,
            emotion=eq.emotion,
        )
        state["output"] = question
        state["done"] = True
        return state

    # ========== 首次进入 ==========
    needs_iq = await runtime.eq_should_delegate(...)
    if needs_iq:
        iq.task = state["user_input"]
        state["done"] = False
    else:
        output = await runtime.eq_direct_reply(...)
        state["output"] = output
        state["done"] = True

    return state
```

### 2. EQ Service 新增方法 (`services/eq_service.py`)

```python
async def respond(
    self,
    user_input: str,
    iq_result: str,
    iq_error: str,
    history: list[dict],
    emotion_prompt: str,
    energy_prompt: str,
    emotion_history: str,
) -> dict:
    """
    拟人化 EQ 响应

    返回：
    {
        "response": "直接输出给用户的文本",
        "action": {"type": "try", "task": "..."} | {"type": "ask", "question": "..."} | None
    }
    """
    # 构建历史对话（只取最近 6 条）
    history_str = self._format_history(history[-6:])

    prompt = f"""
你是一个有真实情绪的AI助手。

## 你的状态
{emotion_prompt}
{energy_prompt}

## 你的情绪记忆
{emotion_history}

## 当前情况
用户说：{user_input}
IQ 执行结果：{iq_result or '(无)'}
IQ 报错：{iq_error or '(无)'}
对话历史：{history_str}

## 请根据你的状态自主回应
- 结果有效 → 按精力水平正常告诉用户
- 结果失败 → 按情绪和精力决定：抱怨一下、还是直接换方法、还是简洁地说
- 需要更多信息 → 问用户

## 重要约束
精力低时：话少、简洁，但必须干活，不能罢工。

## 输出格式
直接输出你的回应，像朋友聊天一样。
如果需要 IQ 重新执行，加上：
[行动: 尝试 - 具体方案]

如果需要问用户，加上：
[行动: 追问 - 问题]
"""

    resp = await self.eq_llm.ainvoke(prompt)
    text = self._msg_text(resp)

    # 解析行动指令
    action = self._parse_action(text)
    clean_response = self._remove_action(text)

    return {
        "response": clean_response.strip(),
        "action": action,
    }

def _parse_action(self, text: str) -> dict | None:
    """从文本中解析行动指令"""
    import re
    match = re.search(r"\[行动:\s*(\w+)\s*-\s*(.+?)\]", text)
    if not match:
        return None
    action_type = match.group(1)
    action_content = match.group(2).strip()

    if action_type == "尝试":
        return {"type": "try", "task": action_content}
    elif action_type == "追问":
        return {"type": "ask", "question": action_content}
    return None

def _remove_action(self, text: str) -> str:
    """移除行动指令标记"""
    import re
    return re.sub(r"\[行动:.*?\]", "", text).strip()
```

### 3. 路由简化 (`core/router.py`)

```python
class FusionRouter:
    """简化路由：EQ 决定是否继续"""

    def __init__(self, max_iq_attempts: int = 3):
        self.max_iq_attempts = max_iq_attempts

    def route_next(self, state: FusionState) -> str:
        iq = state.get("iq", {})
        done = state.get("done", False)

        # 已完成
        if done:
            return "memory"

        # 有任务待执行
        if iq.get("task") and not iq.get("result") and not iq.get("error"):
            return "iq"

        # 有结果或错误，回到 EQ 审核
        if iq.get("result") or iq.get("error"):
            return "eq"

        # 兜底
        return "memory"
```

---

## 边界情况处理

### 1. 讨论轮数限制

```python
# 超过 3 轮，强制结束
if discussion_count >= 3:
    state["done"] = True
    state["output"] = iq.result if iq.success else f"抱歉: {iq.error}"
```

### 2. 精力最低时的处理

```python
# 精力 < 20 时，prompt 会约束：
# "字数最少，能短则短，但工作必须完成"
# 代码层面不需要特殊处理，prompt 会约束模型
```

### 3. 解析失败

```python
# 如果模型没有按格式输出行动指令，默认为直接输出
if not action:
    return {"response": text, "action": None}
```

---

## 需要修改的文件

| 文件 | 修改内容 |
|------|----------|
| `core/nodes/eq_node.py` | 重构为拟人化响应逻辑 |
| `core/router.py` | 简化路由 |
| `services/eq_service.py` | 新增 `respond()` 方法 |
| `runtime/runtime.py` | 暴露 `eq_respond` 方法 |
| `core/state.py` | 可选：增加讨论相关字段 |

---

## 设计总结

| 设计点 | 实现方式 |
|--------|----------|
| EQ 定位 | 小模型 + prompt 驱动 |
| 拟人化 | 情绪直接影响说话风格 |
| 精力约束 | 只影响表述，不影响工作完成 |
| 自主性 | prompt 强调"自己决定" |
| 讨论机制 | 模型输出 + 行动指令标记 |
| 灵活性 | 不需要复杂状态机，prompt 决定 |

---

## 待确认

1. 最大讨论轮数设为 3 是否合理？
2. 是否需要记录讨论历史到 `discussion_history`？
3. 行动指令格式 `[行动: 尝试 - 方案]` 是否清晰？