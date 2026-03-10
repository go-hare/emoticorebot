"""Main-brain service for deliberation and final user-facing decisions."""

from __future__ import annotations

import json
import re
from typing import Any

from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.core.state import MainBrainControlPacket, MainBrainDeliberationPacket, MainBrainFinalizePacket
from emoticorebot.utils.llm_utils import extract_message_metrics, extract_message_text

try:
    from deepagents import create_deep_agent
except Exception:
    create_deep_agent = None


class MainBrainService:
    """Drive the main-brain pass before and after executor work."""

    def __init__(self, brain_llm, context_builder: ContextBuilder):
        self.brain_llm = brain_llm
        self.context = context_builder

    @staticmethod
    def _compact_text(text: Any, limit: int = 160) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "..."

    async def generate_proactive(
        self,
        prompt: str,
        *,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> str:
        del channel, chat_id, session_id
        system_prompt = self.context.build_main_brain_system_prompt(query=prompt)
        response = await self.brain_llm.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
        )
        return extract_message_text(response).strip()

    async def deliberate(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> MainBrainDeliberationPacket:
        lightweight_chat = not self._looks_task_like(user_input)
        if lightweight_chat:
            prompt = f"""
你是 `main_brain`，正在为当前轮做第一次内部决策。

这一轮更像陪伴聊天或轻量对话，因此默认应直接回复，不调用 `executor`。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `intent`：你对用户当前意图的简短理解。
- `working_hypothesis`：你当前的工作性判断，1 句话即可。
- `execution_action`：这里只能填 `answer`。
- `execution_reason`：为什么这轮不需要调用 `executor`。
- `final_decision`：这里只能填 `answer`。
- `question_to_executor`：必须为空字符串 `""`。
- `final_message`：真正要回给用户的话，必须使用与用户相同的语言。

硬性规则：
1. `execution_action` 必须是 `answer`。
2. `final_decision` 必须是 `answer`。
3. `question_to_executor` 必须是空字符串。
4. `final_message` 必须非空。
5. 不要遗漏任何字段。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "execution_action": "answer",
  "execution_reason": "...",
  "final_decision": "answer",
  "question_to_executor": "",
  "final_message": "..."
}}

示例：
{{
  "intent": "用户在轻松聊天，希望得到自然回应",
  "working_hypothesis": "当前不需要外部工具或复杂执行",
  "execution_action": "answer",
  "execution_reason": "这是轻量对话，主脑可直接完成回复",
  "final_decision": "answer",
  "question_to_executor": "",
  "final_message": "当然可以呀，我在这儿陪你聊。"
}}

用户输入：{user_input}
""".strip()
        else:
            prompt = f"""
你是 `main_brain`，正在为当前轮做第一次内部决策。

请先深入理解用户，再决定：
- 直接回复；或
- 调用 `executor` 去完成事实核查、工具执行、多步求解。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `intent`：你对用户当前意图的简要理解。
- `working_hypothesis`：你目前对问题的工作性判断。
- `execution_action`：只能是 `start` 或 `answer`。
- `execution_reason`：为什么要直接回复，或为什么要启动 `executor`。
- `final_decision`：如果启动 `executor`，必须是 `continue`；如果直接回复，必须是 `answer`。
- `question_to_executor`：发给 `executor` 的内部请求。只有在 `execution_action=start` 时填写。
- `final_message`：只有在 `execution_action=answer` 时填写，必须使用与用户相同的语言。

硬性规则：
1. 如果 `execution_action` = `start`：
   - `final_decision` 必须是 `continue`
   - `question_to_executor` 必须非空
   - `final_message` 必须是空字符串 `""`
2. 如果 `execution_action` = `answer`：
   - `final_decision` 必须是 `answer`
   - `question_to_executor` 必须是空字符串 `""`
   - `final_message` 必须非空
3. 不要遗漏任何字段。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "execution_action": "start|answer",
  "execution_reason": "...",
  "final_decision": "continue|answer",
  "question_to_executor": "...",
  "final_message": "..."
}}

启动 executor 示例：
{{
  "intent": "用户希望解决一个需要工具和多步分析的问题",
  "working_hypothesis": "仅靠主脑当前上下文不足以保证结果准确，需要执行系统补齐事实",
  "execution_action": "start",
  "execution_reason": "需要调用工具并进行多步执行",
  "final_decision": "continue",
  "question_to_executor": "请检查当前问题需要哪些工具，完成分析并返回最终执行结果、风险和缺失信息。",
  "final_message": ""
}}

直接回复示例：
{{
  "intent": "用户在表达情绪并希望被承接",
  "working_hypothesis": "当前更适合由主脑直接回应，不需要执行系统介入",
  "execution_action": "answer",
  "execution_reason": "这轮主要是理解和回应，不需要工具或事实核查",
  "final_decision": "answer",
  "question_to_executor": "",
  "final_message": "我在，先别急，你可以慢慢跟我说。"
}}

用户输入：{user_input}
""".strip()

        raw_text, metrics = await self._run_main_brain_task(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_executor_summaries=None,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
            query=user_input,
            retrieval_focus=["user", "relationship"] if lightweight_chat else ["user", "goal", "constraint", "tool", "skill"],
        )

        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_deliberation(raw_text)
            if recovered is not None:
                recovered.update(metrics)
                return recovered
            fallback = self._fallback_deliberation(user_input=user_input, emotion=emotion)
            fallback.update(metrics)
            return fallback

        normalized = self._normalize_deliberation_payload(parsed)
        if normalized is None:
            fallback = self._fallback_deliberation(user_input=user_input, emotion=emotion)
            fallback.update(metrics)
            return fallback
        normalized.update(metrics)
        return normalized

    async def finalize(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        main_brain_intent: str,
        main_brain_working_hypothesis: str,
        executor_summary: str,
        executor_status: str,
        executor_missing: list[str],
        executor_recommended_action: str,
        loop_count: int,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> MainBrainFinalizePacket:
        prompt = f"""
你是 `main_brain`，正在读取本轮 `executor` 结果并做最终决策。

请综合：
- 你的初始判断
- 当前 `executor` 返回结果
- 用户真实需求

然后只在以下三种决策中选择一种：
- `answer`：已经可以直接对用户回复
- `ask_user`：必须让用户补充信息
- `continue`：还需要继续让 `executor` 往下执行

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `final_decision`：只能是 `answer`、`ask_user`、`continue`。
- `final_message`：当决策为 `answer` 或 `ask_user` 时，要给用户看的话；必须使用与用户相同的语言。
- `question_to_executor`：当决策为 `continue` 时，发给 `executor` 的下一条内部问题。

硬性规则：
1. 如果 `final_decision` = `continue`：
   - `question_to_executor` 必须非空
   - `final_message` 必须是空字符串 `""`
2. 如果 `final_decision` = `answer` 或 `ask_user`：
   - `final_message` 必须非空
   - `question_to_executor` 必须是空字符串 `""`
3. 不要遗漏任何字段。

标准结构：
{{
  "final_decision": "answer|ask_user|continue",
  "final_message": "...",
  "question_to_executor": "..."
}}

继续执行示例：
{{
  "final_decision": "continue",
  "final_message": "",
  "question_to_executor": "请继续处理尚未解决的部分，补齐关键缺失信息后返回最终结果。"
}}

要求用户补充示例：
{{
  "final_decision": "ask_user",
  "final_message": "我还缺一个关键信息：你希望我以哪个时间范围来查询？",
  "question_to_executor": ""
}}

直接回复示例：
{{
  "final_decision": "answer",
  "final_message": "我先把当前能确认的结论告诉你：这个方向是可行的。",
  "question_to_executor": ""
}}

用户输入：{user_input}
主脑意图：{main_brain_intent or '（空）'}
主脑工作假设：{self._compact_text(main_brain_working_hypothesis, limit=140) or '（空）'}
executor 摘要：{self._compact_text(executor_summary, limit=320) or '（空）'}
当前循环次数：{loop_count}
""".strip()

        raw_text, metrics = await self._run_main_brain_task(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_executor_summaries=[executor_summary] if executor_summary else None,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
            query=(f"{user_input}\n{executor_summary}".strip()),
            retrieval_focus=["user", "goal", "constraint", "tool", "skill"],
        )

        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_finalize(raw_text)
            if recovered is not None:
                recovered.update(metrics)
                return recovered
            fallback = self._fallback_finalize(
                executor_status=executor_status,
                executor_analysis=executor_summary,
                executor_missing=executor_missing,
                executor_recommended_action=executor_recommended_action,
            )
            fallback.update(metrics)
            return fallback

        normalized = self._normalize_finalize_payload(parsed)
        if normalized is None:
            fallback = self._fallback_finalize(
                executor_status=executor_status,
                executor_analysis=executor_summary,
                executor_missing=executor_missing,
                executor_recommended_action=executor_recommended_action,
            )
            fallback.update(metrics)
            return fallback
        normalized.update(metrics)
        return normalized

    def decide_paused_execution(
        self,
        *,
        user_input: str,
        execution: dict[str, Any],
        emotion: str,
    ) -> MainBrainControlPacket:
        action, reason = self._decide_paused_execution_action(
            user_input=user_input,
            execution=execution,
            emotion=emotion,
        )
        if action == "resume":
            return {
                "action": "resume",
                "reason": reason,
                "execution": dict(execution or {}),
            }
        if action == "defer":
            return {
                "action": "defer",
                "reason": reason,
                "execution": self._strip_resume_payload(execution),
            }
        return {
            "action": "pause",
            "reason": reason,
            "final_decision": "answer",
            "message": self._build_paused_execution_hold_message(
                user_input=user_input,
                execution=execution,
                reason=reason,
                emotion=emotion,
            ),
            "execution": self._strip_resume_payload(execution),
        }

    def control_after_deliberation(
        self,
        *,
        deliberation: MainBrainDeliberationPacket,
        emotion: str,
    ) -> MainBrainControlPacket:
        execution_action = str(deliberation.get("execution_action", "") or "").strip().lower()
        execution_reason = str(deliberation.get("execution_reason", "") or "").strip()
        if execution_action == "start" or deliberation.get("need_executor"):
            return {
                "action": "start",
                "reason": execution_reason or "main_brain_requested_executor",
                "question_to_executor": str(
                    deliberation.get("question_to_executor", "")
                    or self._build_default_executor_question(
                        working_hypothesis=str(deliberation.get("working_hypothesis", "") or ""),
                        intent=str(deliberation.get("intent", "") or ""),
                    )
                ).strip(),
            }
        return {
            "action": "answer",
            "reason": execution_reason or "main_brain_answered_directly",
            "final_decision": "answer",
            "message": str(deliberation.get("final_message", "") or "").strip() or build_companion_prompt(emotion),
        }

    def control_after_finalize(
        self,
        *,
        finalize: MainBrainFinalizePacket,
        loop_count: int,
        max_loop_rounds: int,
        executor_control_state: str,
        executor_status: str,
        executor_missing: list[str],
        executor_analysis: str,
        executor_risks: list[str],
    ) -> MainBrainControlPacket:
        decision = str(finalize.get("final_decision", "") or finalize.get("decision", "") or "answer").strip().lower()
        message = str(finalize.get("final_message", "") or finalize.get("message", "") or "").strip()
        question_to_executor = str(finalize.get("question_to_executor", "") or "").strip()

        if decision == "continue":
            if loop_count >= max_loop_rounds:
                forced_decision, forced_message = self._force_complete(
                    executor_status=executor_status,
                    executor_missing=executor_missing,
                    executor_analysis=executor_analysis,
                )
                return {
                    "action": "pause" if executor_control_state == "paused" else "answer",
                    "reason": "loop_limit_reached",
                    "final_decision": forced_decision,
                    "message": forced_message,
                }
            return {
                "action": "continue",
                "reason": "main_brain_requested_executor_followup",
                "final_decision": "continue",
                "question_to_executor": question_to_executor
                or self._build_followup_executor_question(
                    executor_risks=executor_risks,
                    executor_analysis=executor_analysis,
                ),
            }

        if decision == "ask_user":
            return {
                "action": "pause" if executor_control_state == "paused" else "answer",
                "reason": "executor_waiting_for_user_input"
                if executor_control_state == "paused"
                else "main_brain_requested_user_input",
                "final_decision": "ask_user",
                "message": message or build_missing_info_prompt(executor_missing),
            }

        return {
            "action": "answer",
            "reason": "executor_result_finalized"
            if executor_control_state == "completed"
            else "main_brain_answered_from_executor",
            "final_decision": "answer",
            "message": message or executor_analysis or "我先给你一个当前能确认的结论，我们可以继续往下推进。",
        }

    def control_stop_execution(
        self,
        *,
        cancelled_tasks: int,
        execution: dict[str, Any] | None = None,
    ) -> MainBrainControlPacket:
        del execution
        message = f"⏹ 已停止 {max(0, int(cancelled_tasks))} 个主任务"
        message += "。"
        return {
            "action": "stop",
            "reason": "user_requested_stop",
            "final_decision": "answer",
            "message": message,
        }

    def build_executor_delegation(
        self,
        *,
        action: str,
        user_input: str,
        question_to_executor: str,
        intent: str,
        working_hypothesis: str,
        session_id: str = "",
        loop_count: int = 0,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        execution = dict(execution or {})
        goal = str(question_to_executor or "").strip() or self._build_default_executor_question(
            working_hypothesis=working_hypothesis,
            intent=intent,
        )
        bundle_query_parts = [goal, user_input, intent, working_hypothesis, str(execution.get("summary", "") or "")]
        bundle = self.context.build_executor_memory_bundle(
            query="\n".join(part for part in bundle_query_parts if str(part).strip()),
            limit=6,
        )
        missing = [str(item).strip() for item in list(execution.get("missing", []) or []) if str(item).strip()]
        constraints = [
            "不要生成最终面向用户的回复。",
            "不要直接检索或写入长期记忆。",
            "只使用 main_brain 已经提供的相关记忆包。",
            "只返回最终执行结果，并保持事实紧凑、建议明确。",
            "遵守工作区、工具和审批边界。",
        ]
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "continue":
            constraints.append("只继续处理当前尚未解决的部分。")
        if normalized_action == "resume":
            constraints.append("基于提供的运行时上下文恢复已暂停的执行。")
        if missing:
            constraints.append("如果条件允许，优先解决当前已知的缺失输入。")

        delegation = {
            "goal": goal,
            "request": goal,
            "constraints": constraints,
            "relevant_execution_memories": list(bundle.get("relevant_execution_memories", []) or []),
            "relevant_tool_memories": list(bundle.get("relevant_tool_memories", []) or []),
            "skill_hints": list(bundle.get("skill_hints", []) or []),
            "success_criteria": [
                "返回一个 main_brain 可以直接吸收的最终执行结果。",
                "只有在确实影响完成度时，才列出阻塞风险或缺失输入。",
                "尽量减少内部往返，能在 executor 内部收敛就不要再拆分。",
            ],
            "return_contract": {
                "mode": "final_only",
                "must_not": ["direct_user_reply", "memory_retrieval", "memory_write"],
            },
        }
        resume_payload = execution.get("resume_payload")
        if resume_payload not in (None, "", [], {}):
            delegation["resume_payload"] = resume_payload
        return delegation

    def decide_deep_reflection(
        self,
        *,
        state: dict[str, Any],
        importance: float,
        execution: dict[str, Any],
        turn_reflection: dict[str, Any],
    ) -> tuple[bool, str]:
        main_brain = state.get("main_brain")
        execution_review = (
            turn_reflection.get("execution_review")
            if isinstance(turn_reflection, dict) and isinstance(turn_reflection.get("execution_review"), dict)
            else {}
        )
        control_state = str(execution.get("control_state", "") or "").strip().lower()
        status = str(execution.get("status", "") or "").strip().lower()
        missing = [str(item).strip() for item in list(execution.get("missing", []) or []) if str(item).strip()]
        pending_review = execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {}
        effectiveness = str((execution_review or {}).get("effectiveness", "none") or "none").strip().lower()
        failure_reason = str((execution_review or {}).get("main_failure_reason", "") or "").strip()
        user_updates = [str(item).strip() for item in list(turn_reflection.get("user_updates", []) or []) if str(item).strip()]
        soul_updates = [str(item).strip() for item in list(turn_reflection.get("soul_updates", []) or []) if str(item).strip()]
        memory_candidates = list(turn_reflection.get("memory_candidates", []) or []) if isinstance(turn_reflection, dict) else []
        execution_reason = str(getattr(main_brain, "execution_reason", "") or "").strip() if main_brain is not None else ""

        if execution.get("invoked") and (status in {"failed", "need_more"} or control_state == "paused"):
            return True, f"main_brain_execution_followup:{control_state or status}"
        if execution.get("invoked") and (missing or pending_review):
            return True, "main_brain_execution_blocked_or_waiting_review"
        if execution.get("invoked") and effectiveness in {"low", "medium"} and failure_reason:
            return True, f"main_brain_execution_review:{failure_reason}"
        if importance >= 0.82 and (user_updates or soul_updates):
            return True, "main_brain_high_importance_identity_updates"
        if importance >= 0.82 and memory_candidates:
            return True, "main_brain_high_importance_memory_candidates"
        if execution_reason in {
            "loop_limit_reached",
            "main_brain_requested_executor_followup",
            "executor_waiting_for_user_input",
        }:
            return True, f"main_brain_signal:{execution_reason}"
        return False, ""

    async def _run_main_brain_task(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str,
        pad_state: tuple[float, float, float] | None,
        internal_executor_summaries: list[str] | None,
        channel: str,
        chat_id: str,
        session_id: str,
        query: str,
        retrieval_focus: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        del channel, chat_id, session_id
        records = self.context.query_main_brain_memories(query=query, limit=8)
        messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_executor_summaries=internal_executor_summaries,
            query=query,
        )
        response = await self.brain_llm.ainvoke(messages)
        metrics = extract_message_metrics(response)
        metrics.update(
            {
                "retrieval_query": query,
                "retrieval_focus": list(retrieval_focus or []),
                "retrieved_memory_ids": [str(record.get("id", "") or "") for record in records if str(record.get("id", "") or "")],
            }
        )
        return extract_message_text(response), metrics

    def _build_agent(self, system_prompt: str):
        if create_deep_agent is None:
            raise RuntimeError("deepagents is not available")
        try:
            return create_deep_agent(
                model=self.brain_llm,
                tools=[],
                system_prompt=system_prompt,
            )
        except TypeError as exc:
            raise RuntimeError(f"Deep Agents API mismatch: {exc}") from exc

    async def _invoke_deep_agent(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        channel: str,
        chat_id: str,
        session_id: str,
    ) -> Any:
        agent = self._build_agent(system_prompt)
        payload = {"messages": messages}
        config = {
            "configurable": {
                "thread_id": self._build_thread_id(
                    channel=channel,
                    chat_id=chat_id,
                    session_id=session_id,
                )
            },
            "metadata": {
                "assistant_id": "emoticorebot-main-brain",
            },
        }
        if hasattr(agent, "astream"):
            return await self._collect_stream_values(agent, payload=payload, config=config)
        if hasattr(agent, "ainvoke"):
            return await agent.ainvoke(payload, config=config)
        if hasattr(agent, "invoke"):
            return agent.invoke(payload, config=config)
        raise RuntimeError("Deep Agent does not expose invoke/ainvoke/astream")

    async def _collect_stream_values(self, agent: Any, *, payload: dict[str, Any], config: dict[str, Any]) -> Any:
        last_values: Any | None = None
        async for item in agent.astream(payload, config=config, stream_mode=["values"], subgraphs=True):
            if isinstance(item, tuple):
                if len(item) == 3:
                    _namespace, mode, data = item
                    if str(mode) == "values":
                        last_values = data
                    continue
                if len(item) == 2:
                    head, tail = item
                    if str(head) == "values":
                        last_values = tail
                        continue
                    if isinstance(head, (list, tuple)):
                        last_values = tail
                        continue
            last_values = item
        if last_values is None:
            raise RuntimeError("Deep Agent stream did not produce final state")
        return last_values

    @staticmethod
    def _build_thread_id(*, channel: str, chat_id: str, session_id: str) -> str:
        base = str(session_id or "").strip()
        if not base:
            channel_text = str(channel or "").strip()
            chat_text = str(chat_id or "").strip()
            base = f"{channel_text}:{chat_text}" if channel_text or chat_text else "default"
        return f"brain:{base}"

    @staticmethod
    def _extract_result_metrics(raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            messages = raw_result.get("messages")
            if isinstance(messages, list) and messages:
                return extract_message_metrics(messages[-1])
        return extract_message_metrics(raw_result)

    @staticmethod
    def _extract_text(raw_result: Any) -> str:
        if raw_result is None:
            return ""
        if isinstance(raw_result, str):
            return raw_result
        if isinstance(raw_result, dict):
            messages = raw_result.get("messages")
            if isinstance(messages, list) and messages:
                last = messages[-1]
                if isinstance(last, dict):
                    content = last.get("content", "")
                    if isinstance(content, list):
                        return " ".join(str(item) for item in content if item)
                    return str(content or "")
                content = getattr(last, "content", "")
                if isinstance(content, list):
                    return " ".join(str(item) for item in content if item)
                return str(content or "")
            for key in ("output", "content", "answer", "result"):
                value = raw_result.get(key)
                if value:
                    return str(value)
            try:
                return json.dumps(raw_result, ensure_ascii=False)
            except Exception:
                return str(raw_result)
        content = getattr(raw_result, "content", "")
        if isinstance(content, list):
            return " ".join(str(item) for item in content if item)
        if content:
            return str(content)
        return str(raw_result)

    @classmethod
    def _normalize_deliberation_payload(
        cls, parsed: dict[str, Any]
    ) -> MainBrainDeliberationPacket | None:
        if not isinstance(parsed, dict):
            return None

        intent = str(parsed.get("intent", "") or "").strip()
        working_hypothesis = str(parsed.get("working_hypothesis", "") or "").strip()
        execution_action = str(parsed.get("execution_action", "") or "").strip().lower()
        execution_reason = str(parsed.get("execution_reason", "") or "").strip()
        final_decision = str(parsed.get("final_decision", "") or "").strip().lower()
        need_executor = parsed.get("need_executor")
        if execution_action not in {"start", "answer"}:
            if isinstance(need_executor, bool):
                execution_action = "start" if need_executor else "answer"
            else:
                return None
        if final_decision not in {"answer", "continue"}:
            final_decision = "continue" if execution_action == "start" else "answer"

        question_to_executor = str(parsed.get("question_to_executor", "") or "").strip()
        final_message = str(parsed.get("final_message", "") or "").strip()

        if execution_action == "start":
            final_decision = "continue"
        else:
            final_decision = "answer"

        if execution_action == "start" and not question_to_executor:
            question_to_executor = working_hypothesis or intent
        if execution_action == "answer" and not final_message:
            return None

        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "execution_action": execution_action,
            "execution_reason": execution_reason,
            "final_decision": final_decision,
            "need_executor": execution_action == "start",
            "question_to_executor": question_to_executor if execution_action == "start" else "",
            "final_message": "" if execution_action == "start" else final_message,
        }

    @classmethod
    def _normalize_finalize_payload(cls, parsed: dict[str, Any]) -> MainBrainFinalizePacket | None:
        if not isinstance(parsed, dict):
            return None

        decision = str(parsed.get("final_decision", "") or parsed.get("decision", "") or "").strip().lower()
        if decision not in {"answer", "ask_user", "continue"}:
            return None

        message = str(parsed.get("final_message", "") or parsed.get("message", "") or "").strip()
        question_to_executor = str(parsed.get("question_to_executor", "") or "").strip()
        if decision == "continue" and not question_to_executor:
            question_to_executor = message
        if decision != "continue":
            question_to_executor = ""
        if decision in {"answer", "ask_user"} and not message:
            return None

        return {
            "final_decision": decision,
            "final_message": message,
            "decision": decision,
            "message": message,
            "question_to_executor": question_to_executor,
        }

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        raw = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw).strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _extract_json_string_field(raw: str, field: str) -> str:
        pattern = rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"'
        match = re.search(pattern, raw, flags=re.DOTALL)
        if not match:
            return ""
        value = match.group(1)
        try:
            return json.loads(f'"{value}"')
        except Exception:
            return value.replace("\\n", "\n").replace('\\"', '"').strip()

    @staticmethod
    def _extract_json_bool_field(raw: str, field: str) -> bool | None:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', raw, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).lower() == "true"

    @classmethod
    def _recover_deliberation(cls, raw: str) -> MainBrainDeliberationPacket | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        execution_action = cls._extract_json_string_field(cleaned, "execution_action").lower()
        execution_reason = cls._extract_json_string_field(cleaned, "execution_reason")
        final_decision = cls._extract_json_string_field(cleaned, "final_decision").lower()
        need_executor = cls._extract_json_bool_field(cleaned, "need_executor")
        intent = cls._extract_json_string_field(cleaned, "intent")
        working_hypothesis = cls._extract_json_string_field(cleaned, "working_hypothesis")
        question_to_executor = cls._extract_json_string_field(cleaned, "question_to_executor")
        final_message = cls._extract_json_string_field(cleaned, "final_message")
        if execution_action == "start" and question_to_executor:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "execution_action": "start",
                "execution_reason": execution_reason,
                "final_decision": "continue",
                "need_executor": True,
                "question_to_executor": question_to_executor,
                "final_message": "",
            }
        if execution_action == "answer" and final_message:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "execution_action": "answer",
                "execution_reason": execution_reason,
                "final_decision": "answer",
                "need_executor": False,
                "question_to_executor": "",
                "final_message": final_message,
            }
        if need_executor is True and question_to_executor:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "need_executor": True,
                "question_to_executor": question_to_executor,
                "final_message": "",
            }
        if need_executor is False and final_message:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "need_executor": False,
                "question_to_executor": "",
                "final_message": final_message,
            }
        return None

    @classmethod
    def _recover_finalize(cls, raw: str) -> MainBrainFinalizePacket | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        decision = cls._extract_json_string_field(cleaned, "final_decision") or cls._extract_json_string_field(cleaned, "decision") or "answer"
        message = cls._extract_json_string_field(cleaned, "final_message") or cls._extract_json_string_field(cleaned, "message")
        question_to_executor = cls._extract_json_string_field(cleaned, "question_to_executor")
        if decision not in {"answer", "ask_user", "continue"}:
            decision = "answer"
        if decision == "continue":
            if not question_to_executor:
                return None
            return {
                "final_decision": decision,
                "final_message": "",
                "decision": decision,
                "message": "",
                "question_to_executor": question_to_executor,
            }
        if not message:
            return None
        return {
            "final_decision": decision,
            "final_message": message,
            "decision": decision,
            "message": message,
            "question_to_executor": "",
        }

    @staticmethod
    def _looks_task_like(user_input: str) -> bool:
        text = (user_input or "").lower()
        keywords = [
            "help me",
            "please",
            "find",
            "search",
            "plan",
            "remind",
            "schedule",
            "analyze",
            "generate",
            "run",
            "open",
            "weather",
            "price",
            "how",
            "why",
            "?",
            "？",
            "帮我",
            "请",
            "查",
            "搜索",
            "计划",
            "提醒",
            "安排",
            "分析",
            "生成",
            "运行",
            "打开",
            "天气",
            "价格",
            "怎么",
            "为什么",
        ]
        return any(keyword in text for keyword in keywords)

    @classmethod
    def _fallback_deliberation(
        cls,
        *,
        user_input: str,
        emotion: str = "平静",
    ) -> MainBrainDeliberationPacket:
        if cls._looks_task_like(user_input):
            return {
                "intent": "用户需要事实分析、执行帮助，或更强的问题求解。",
                "working_hypothesis": "在给出最终表达前，需要先调用 executor 补齐事实与执行判断。",
                "execution_action": "start",
                "execution_reason": "main_brain_requested_executor",
                "final_decision": "continue",
                "need_executor": True,
                "question_to_executor": "请分析用户请求需要的事实、工具、风险与最合适的下一步。",
                "final_message": "",
            }
        return {
            "intent": "用户当前更需要陪伴式回应或轻量交流。",
            "working_hypothesis": "这一轮无需调用 executor。",
            "execution_action": "answer",
            "execution_reason": "main_brain_answered_directly",
            "final_decision": "answer",
            "need_executor": False,
            "question_to_executor": "",
            "final_message": build_companion_prompt(emotion),
        }

    @classmethod
    def _fallback_finalize(
        cls,
        *,
        executor_status: str,
        executor_analysis: str,
        executor_missing: list[str],
        executor_recommended_action: str,
    ) -> MainBrainFinalizePacket:
        if (
            executor_missing
            or executor_status == "need_more"
            or executor_recommended_action == "ask_user"
        ):
            return {
                "final_decision": "ask_user",
                "final_message": build_missing_info_prompt(executor_missing),
                "decision": "ask_user",
                "message": build_missing_info_prompt(executor_missing),
                "question_to_executor": "",
            }
        if executor_recommended_action == "continue":
            return {
                "final_decision": "continue",
                "final_message": "",
                "decision": "continue",
                "message": "",
                "question_to_executor": "请补上最关键的证据缺口、主要风险，以及最稳妥的下一步。",
            }
        return {
            "final_decision": "answer",
            "final_message": executor_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
            "decision": "answer",
            "message": executor_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
            "question_to_executor": "",
        }

    @staticmethod
    def _build_default_executor_question(*, working_hypothesis: str, intent: str) -> str:
        if working_hypothesis:
            return (
                "Analyze the current working hypothesis, identify evidence, risks, "
                f"and the best next action: {working_hypothesis}"
            )
        if intent:
            return f"Analyze this user intent, identify evidence, risks, and the best next action: {intent}"
        return "Analyze the current internal question and return evidence, risks, and the best next action."

    @staticmethod
    def _build_followup_executor_question(*, executor_risks: list[str], executor_analysis: str) -> str:
        if executor_risks:
            risk_text = "; ".join(str(item).strip() for item in executor_risks[:2] if str(item).strip())
            if risk_text:
                return f"Focus on these key risks and produce a more robust next step: {risk_text}"
        if executor_analysis:
            return f"Strengthen the weakest part of this analysis and make the next action clearer: {executor_analysis}"
        return "Fill the most important evidence gaps and provide the next action."

    @staticmethod
    def _force_complete(*, executor_status: str, executor_missing: list[str], executor_analysis: str) -> tuple[str, str]:
        if executor_status == "need_more" or executor_missing:
            return "ask_user", build_missing_info_prompt(executor_missing)
        if executor_analysis:
            return "answer", executor_analysis
        return "answer", "我先给你一个阶段性结论，不过还需要更多信息才能更稳。"

    @staticmethod
    def _strip_resume_payload(execution: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(execution or {})
        cleaned.pop("resume_payload", None)
        return cleaned

    @classmethod
    def _decide_paused_execution_action(
        cls,
        *,
        user_input: str,
        execution: dict[str, Any],
        emotion: str,
    ) -> tuple[str, str]:
        text = str(user_input or "").strip()
        resume_payload = execution.get("resume_payload")
        pending_review = dict(execution.get("pending_review", {}) or {})
        missing = [str(item).strip() for item in (execution.get("missing", []) or []) if str(item).strip()]

        if cls._looks_like_pause_request(text):
            return "pause", "user_requested_pause"
        if resume_payload not in (None, "", [], {}):
            if pending_review:
                return "resume", "user_responded_to_pending_review"
            if missing:
                return "resume", "user_provided_missing_information"
            return "resume", "user_requested_resume"
        if cls._looks_like_resume_request(text):
            if missing:
                return "resume", "user_requested_resume_for_missing_information"
            if not pending_review:
                return "resume", "user_requested_resume"
        if cls._looks_like_companionship_or_explanation(text, emotion=emotion):
            return "pause", "main_brain_prioritized_companionship_or_explanation"
        if cls._looks_like_priority_switch(text):
            return "defer", "user_switched_priority"
        if missing:
            if cls._looks_like_new_task_input(text):
                return "defer", "user_started_new_topic_while_execution_paused"
            if text:
                return "resume", "user_provided_missing_information"
        if pending_review and text:
            return "defer", "user_started_new_topic_while_review_paused"
        if text:
            return "defer", "paused_execution_left_on_hold"
        return "pause", "paused_execution_waiting"

    @staticmethod
    def _looks_like_pause_request(text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        exact_matches = {
            "pause",
            "wait",
            "later",
            "stop for now",
            "等等",
            "等下",
            "等一下",
            "先停一下",
            "先暂停",
            "稍等",
            "回头再说",
            "先别继续",
        }
        prefixes = ("pause", "wait", "later", "先停", "先暂停", "稍等", "等会", "回头", "先别继续")
        return lowered in exact_matches or any(lowered.startswith(prefix) for prefix in prefixes)

    @staticmethod
    def _looks_like_resume_request(text: str) -> bool:
        if not text:
            return False
        lowered = text.lower().strip()
        exact_matches = {
            "resume",
            "continue",
            "go ahead",
            "继续",
            "继续吧",
            "继续执行",
            "恢复",
            "恢复执行",
            "接着来",
            "接着做",
        }
        prefixes = (
            "resume",
            "continue",
            "go ahead",
            "继续",
            "恢复",
            "接着",
        )
        return lowered in exact_matches or any(lowered.startswith(prefix) for prefix in prefixes)

    @classmethod
    def _looks_like_new_task_input(cls, text: str) -> bool:
        if not text:
            return False
        if cls._looks_like_priority_switch(text):
            return True
        return cls._looks_task_like(text)

    @staticmethod
    def _looks_like_companionship_or_explanation(text: str, *, emotion: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        emotion_keywords = (
            "难受",
            "崩溃",
            "想哭",
            "焦虑",
            "害怕",
            "不舒服",
            "烦",
            "生气",
            "委屈",
            "累",
            "陪我",
            "安慰",
            "救命",
            "help me calm",
            "anxious",
            "overwhelmed",
        )
        explanation_keywords = (
            "什么意思",
            "为什么",
            "怎么回事",
            "解释",
            "说明一下",
            "先说清楚",
            "看不懂",
            "没懂",
            "what do you mean",
            "explain",
            "why",
        )
        if any(keyword in lowered for keyword in emotion_keywords):
            return True
        if any(keyword in lowered for keyword in explanation_keywords):
            return True
        return emotion not in {"平静", "开心"} and len(text) <= 12 and any(mark in text for mark in ("?", "？"))

    @staticmethod
    def _looks_like_priority_switch(text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        keywords = (
            "先处理这个",
            "先看这个",
            "还有个更急",
            "更急",
            "另外一件",
            "另一个问题",
            "换个事情",
            "换个问题",
            "新任务",
            "urgent",
            "asap",
        )
        return any(keyword in lowered for keyword in keywords)

    @staticmethod
    def _build_paused_execution_hold_message(
        *,
        user_input: str,
        execution: dict[str, Any],
        reason: str,
        emotion: str,
    ) -> str:
        del execution, emotion
        if reason == "user_requested_pause":
            return "好，我先把刚才的执行保持暂停，不继续往下跑。你想恢复时直接跟我说‘继续’就行。"
        if reason == "main_brain_prioritized_companionship_or_explanation":
            if any(token in str(user_input or "").lower() for token in ("什么意思", "解释", "为什么", "怎么回事", "explain", "why")):
                return "好，我先不继续跑刚才的执行。你现在更想让我先解释当前进展，还是你要先补充信息继续？"
            return "好，我先不继续跑刚才的执行，先陪你把现在这部分理顺。你可以直接告诉我，此刻最想先处理的是哪一点。"
        if reason == "user_switched_priority":
            return "好，我先把刚才的执行挂起，不往下推进。你现在更急的这件事，我们先把重点说清楚。"
        return "好，我先保持当前执行暂停。你想恢复时告诉我‘继续’，或直接补充新的信息也可以。"


__all__ = ["MainBrainService"]
