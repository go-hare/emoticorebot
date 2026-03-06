"""EQ Node - 情绪感知 + 输出生成（唯一用户出口）

EQ 是唯一入口，每次调用 eq_respond 自主决定：
- 首次：判断是否需要 IQ 执行
- IQ 完成后：判断接受/重试/追问/直接回复

全部由模型自主判断，代码只处理指令。
"""

from emoticorebot.core.state import FusionState, IQState, EQState


# 最大讨论轮数
MAX_DISCUSSION_ROUNDS = 3


async def eq_node(state: FusionState, runtime) -> FusionState:
    """EQ 节点：唯一入口，自主决策"""
    eq: EQState = state["eq"]
    iq: IQState = state["iq"]
    user_input = state["user_input"]

    # 获取讨论轮数
    discussion_count = state.get("discussion_count", 0)

    # 检查轮数限制
    if discussion_count >= MAX_DISCUSSION_ROUNDS:
        state["done"] = True
        state["output"] = iq.result if iq.result else f"抱歉: {iq.error}"
        return state

    # 调用 eq_respond，让模型自主决策
    response = await runtime.eq_respond(
        user_input=user_input,
        iq_result=iq.result,
        iq_error=iq.error,
        history=eq.discussion_history,
        emotion=eq.emotion,
        pad=eq.pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
    )

    action = response.get("action")

    if action:
        action_type = action.get("type")

        if action_type == "delegate":
            # 需要 IQ 执行
            iq.task = action.get("task", user_input)
            iq.result = ""
            iq.error = ""
            state["done"] = False

        elif action_type == "try":
            # 继续尝试：发新任务给 IQ
            iq.task = action.get("task")
            iq.result = ""
            iq.error = ""
            state["discussion_count"] = discussion_count + 1
            state["done"] = False

        elif action_type == "ask":
            # 追问用户
            state["output"] = response["response"]
            state["done"] = True
    else:
        # 直接输出（接受结果 或 不需要 IQ）
        state["output"] = response["response"]
        state["done"] = True

    return state