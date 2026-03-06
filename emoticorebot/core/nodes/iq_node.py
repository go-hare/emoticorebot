"""IQ Node - 工具调用 + 任务执行。

职责：
1. 接收 EQ 委托的任务
2. 通过工具调用循环完成任务
3. 返回结果或追问需求给 EQ

策略参数由 FusionState.policy 注入：
- fact_depth:   输出详细程度 1-3
- tool_budget:  工具调用次数上限
"""

from emoticorebot.core.state import FusionState, IQState, EQState


async def iq_node(state: FusionState, runtime) -> FusionState:
    iq: IQState = state["iq"]
    eq: EQState = state["eq"]
    task = iq.task
    if not task:
        state["done"] = True
        return state

    policy = state.get("policy")
    metadata = state.get("metadata", {})
    on_progress = state.get("on_progress")

    intent_params = metadata.get("intent_params")
    if not isinstance(intent_params, dict):
        intent_params = None

    if policy:
        fact_depth = policy.fact_depth
        tool_budget = policy.tool_budget
    else:
        # 降级策略：根据任务特征自动判断
        history_len = len(state.get("history", []))
        task_len = len(task)

        if "详细" in task or "完整" in task or task_len > 100:
            fact_depth = 3
        elif "简单" in task or "快速" in task or history_len > 20:
            fact_depth = 1
        else:
            fact_depth = 2

        if "复杂" in task or "多个" in task:
            tool_budget = runtime.max_iterations
        elif "简单" in task or fact_depth == 1:
            tool_budget = max(3, runtime.max_iterations // 2)
        else:
            tool_budget = max(5, runtime.max_iterations)

    result = await runtime.run_iq_task(
        task=task,
        history=state.get("history", []),
        emotion=eq.emotion,
        pad=eq.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        intent_params=intent_params,
        tool_budget=tool_budget,
        fact_depth=fact_depth,
        media=state.get("media"),
        on_progress=on_progress,
    )

    iq.attempts = iq.attempts + 1
    iq.tool_calls = result.get("tool_calls", [])
    iq.iterations = result.get("iterations", 0)
    iq.fact_depth = fact_depth
    iq.tool_budget = tool_budget

    if result.get("requires_more_info"):
        iq.needs_input = True
        iq.missing_params = result.get("missing", [])
        iq.result = ""
        iq.success = False
    else:
        iq.result = result.get("content", "")
        iq.needs_input = False
        iq.success = True

    return state
