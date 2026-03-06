"""EQ Node - 情绪感知 + 输出生成（唯一用户出口）+ IQ 讨论

职责：
1. 感知用户情绪
2. 与 IQ 结果协同（润色/追问/讨论）
3. 生成最终输出文本
4. 决定是否委托给 IQ
5. IQ 失败时自主决策是否重试

执行路径：
- IQ 已返回结果/错误 → 拟人化响应（接受/建议/追问）
- IQ 需要追问    → 生成追问 → state["done"]=True
- 首次进入       → 判断是否委托给 IQ
"""

from emoticorebot.core.state import FusionState, IQState, EQState


# 最大讨论轮数
MAX_DISCUSSION_ROUNDS = 3


async def eq_node(state: FusionState, runtime) -> FusionState:
    """EQ 节点：拟人化情绪响应 + IQ 讨论"""
    eq: EQState = state["eq"]
    iq: IQState = state["iq"]
    emotion = eq.emotion
    pad = eq.pad
    user_input = state["user_input"]

    policy = state.get("policy")
    empathy_depth = policy.empathy_depth if policy else 1

    # 获取讨论轮数
    discussion_count = state.get("discussion_count", 0)

    # ========== Case 1: IQ 执行完成（有 result 或 error）==========
    # 这是核心新增：拟人化响应 + 自主决策
    if iq.result or iq.error:
        # 检查讨论轮数
        if discussion_count >= MAX_DISCUSSION_ROUNDS:
            # 超过最大轮数，强制结束
            state["done"] = True
            if iq.result:
                # 成功，润色输出
                response = await runtime.eq_polish(
                    user_input=user_input,
                    iq_result=iq.result,
                    history=state.get("history", []),
                    emotion=emotion,
                    pad=pad,
                    channel=state.get("channel", ""),
                    chat_id=state.get("chat_id", ""),
                    style="concise",
                )
                state["output"] = response
            else:
                # 失败，输出错误
                state["output"] = f"抱歉，遇到问题: {iq.error}"
            return state

        # 调用拟人化 EQ 响应
        response = await runtime.eq_respond(
            user_input=user_input,
            iq_result=iq.result,
            iq_error=iq.error,
            history=eq.discussion_history,
        )

        # 解析行动指令
        action = response.get("action")

        if action:
            if action["type"] == "try":
                # 继续尝试：发新任务给 IQ
                iq.task = action["task"]
                iq.result = ""
                iq.error = ""
                state["discussion_count"] = discussion_count + 1
                state["done"] = False

                # 记录讨论历史
                eq.discussion_history = eq.discussion_history + [
                    {"role": "iq_result", "content": iq.result or f"错误: {iq.error}"},
                    {"role": "eq_response", "content": response["response"]},
                ]

            elif action["type"] == "ask":
                # 追问用户
                state["output"] = response["response"]
                state["done"] = True
        else:
            # 没有行动指令，直接输出响应
            state["output"] = response["response"]
            state["done"] = True

            # 记录讨论历史
            eq.discussion_history = eq.discussion_history + [
                {"role": "iq_result", "content": iq.result or f"错误: {iq.error}"},
                {"role": "eq_response", "content": response["response"]},
            ]

        return state

    # ========== Case 2: IQ 需要用户补充信息（保持原有）==========
    if iq.needs_input:
        question = await runtime.eq_followup(
            missing=iq.missing_params,
            emotion=emotion,
        )
        eq.pending_question = question
        state["done"] = True
        state["output"] = question
        return state

    # ========== Case 3: 首次进入，判断是否委托 IQ（保持原有）==========
    needs_iq = await runtime.eq_should_delegate(
        user_input=user_input,
        history=state.get("history", []),
        emotion=emotion,
        pad=pad,
        channel=state.get("channel", ""),
        chat_id=state.get("chat_id", ""),
    )
    if needs_iq:
        # 需要 IQ
        empathy_needed = empathy_depth >= 1 and (
            pad.get("pleasure", 0.0) < -0.2
            or "难过" in user_input
            or "着急" in user_input
        )
        if empathy_needed:
            opening = await runtime.eq_empathy(
                user_input=user_input,
                emotion=emotion,
                pad=pad,
            )
            if opening:
                eq.empathy_opening = opening

        iq.task = user_input
        state["done"] = False
    else:
        # 不需要 IQ，直接回复
        state["output"] = await runtime.eq_direct_reply(
            user_input=user_input,
            history=state.get("history", []),
            emotion=emotion,
            pad=pad,
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
        )
        state["done"] = True

    # 更新情绪标签
    eq.emotion = runtime.get_emotion_label(eq.pad)
    return state