"""IQ Node - 工具调用 + 任务执行。

职责：
1. 接收 EQ 委托的任务
2. 通过工具调用循环完成任务
3. 返回结果或追问需求给 EQ
"""

from emoticorebot.core.state import FusionState, IQState, EQState


async def iq_node(state: FusionState, runtime) -> FusionState:
    iq: IQState = state["iq"]
    eq: EQState = state["eq"]
    task = iq.task
    if not task:
        state["done"] = True
        return state

    metadata = state.get("metadata", {})
    on_progress = state.get("on_progress")

    intent_params = metadata.get("intent_params")
    if not isinstance(intent_params, dict):
        intent_params = None

    result = await runtime.run_iq_task(
        task=task,
        history=state.get("history", []),
        emotion=eq.emotion,
        pad=eq.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
        intent_params=intent_params,
        media=state.get("media"),
        on_progress=on_progress,
    )

    iq.attempts = iq.attempts + 1
    iq.tool_calls = result.get("tool_calls", [])
    iq.iterations = result.get("iterations", 0)

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
