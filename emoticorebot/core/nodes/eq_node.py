"""EQ Node - 情绪感知 + 输出生成（唯一用户出口）。

职责：
1. 感知用户情绪
2. 与 IQ 结果协同（润色/追问）
3. 生成最终输出文本
4. 决定是否委托给 IQ

执行路径：
- IQ 已返回结果 → 润色 → state["done"]=True
- IQ 需要追问    → 生成追问 → state["done"]=True
- 首次进入       → 判断是否委托给 IQ
"""

from emoticorebot.core.state import FusionState, IQState, EQState


async def eq_node(state: FusionState, runtime) -> FusionState:
    eq: EQState = state["eq"]
    iq: IQState = state["iq"]
    emotion = eq.emotion
    pad = eq.pad
    user_input = state["user_input"]

    policy = state.get("policy")
    empathy_depth = policy.empathy_depth if policy else 1
    tone = policy.tone if policy else "professional"

    # Case 1: IQ 已返回结果，EQ 负责润色
    if iq.result:
        style = tone
        if style == "warm":
            style = "caring"
        elif style == "balanced":
            style = "professional"

        opening = ""
        task_complexity = len(iq.result)
        if empathy_depth >= 1 and (task_complexity > 500 or pad.get("pleasure", 0.0) < -0.2):
            opening = await runtime.eq_empathy(
                user_input=user_input,
                emotion=emotion,
                pad=pad,
            )

        response = await runtime.eq_polish(
            user_input=user_input,
            iq_result=iq.result,
            history=state.get("history", []),
            emotion=emotion,
            pad=pad,
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
            style=style,
        )

        closing = ""
        if empathy_depth >= 1 and opening and task_complexity > 500:
            closing = "我会陪你把这件事处理好。"

        final_output = "\n\n".join(filter(None, [opening, response, closing]))

        eq.discussion_history = eq.discussion_history + [
            {"role": "iq", "content": iq.result},
            {"role": "eq", "content": final_output},
        ]
        state["done"] = True
        state["output"] = final_output

    # Case 2: IQ 需要用户补充信息
    elif iq.needs_input:
        question = await runtime.eq_followup(
            missing=iq.missing_params,
            emotion=emotion,
        )
        eq.pending_question = question
        state["done"] = True
        state["output"] = question

    # Case 3: 首次进入，判断是否需要委托给 IQ
    else:
        needs_iq = await runtime.eq_should_delegate(
            user_input=user_input,
            history=state.get("history", []),
            emotion=emotion,
            pad=pad,
            channel=state.get("channel", ""),
            chat_id=state.get("chat_id", ""),
        )
        if needs_iq:
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
            state["output"] = await runtime.eq_direct_reply(
                user_input=user_input,
                history=state.get("history", []),
                emotion=emotion,
                pad=pad,
                channel=state.get("channel", ""),
                chat_id=state.get("chat_id", ""),
            )
            state["done"] = True

    eq.emotion = runtime.get_emotion_label(eq.pad)
    return state
