"""Main-brain service for deliberation and final user-facing decisions."""

from __future__ import annotations

import json
import re
from typing import Any

from emoticorebot.agent.context import ContextBuilder
from emoticorebot.agent.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.agent.state import BrainControlPacket, BrainDeliberationPacket, BrainFinalizePacket
from emoticorebot.utils.llm_utils import extract_message_metrics, extract_message_text

try:
    from deepagents import create_deep_agent
except Exception:
    create_deep_agent = None


class BrainService:
    """Drive the brain pass before and after central work."""

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
        system_prompt = self.context.build_brain_system_prompt(query=prompt)
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
    ) -> BrainDeliberationPacket:
        lightweight_chat = not self._looks_task_like(user_input)
        if lightweight_chat:
            prompt = f"""
你是 `brain`，正在为当前轮做第一次内部决策。

这一轮更像陪伴聊天或轻量对话，因此默认应直接回复，不调用 `central`。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `intent`：你对用户当前意图的简短理解。
- `working_hypothesis`：你当前的工作性判断，1 句话即可。
- `task_action`：这里只能填 `none`。
- `task_reason`：为什么这轮不需要调用 `central`。
- `final_decision`：这里只能填 `answer`。
- `task_brief`：必须为空字符串 `""`。
- `final_message`：真正要回给用户的话，必须使用与用户相同的语言。

硬性规则：
1. `task_action` 必须是 `none`。
2. `final_decision` 必须是 `answer`。
3. `task_brief` 必须是空字符串。
4. `final_message` 必须非空。
5. 不要遗漏任何字段。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "task_action": "none",
  "task_reason": "...",
  "final_decision": "answer",
  "task_brief": "",
  "final_message": "..."
}}

示例：
{{
  "intent": "用户在轻松聊天，希望得到自然回应",
  "working_hypothesis": "当前不需要外部工具或复杂执行",
  "task_action": "none",
  "task_reason": "这是轻量对话，主脑可直接完成回复",
  "final_decision": "answer",
  "task_brief": "",
  "final_message": "当然可以呀，我在这儿陪你聊。"
}}

用户输入：{user_input}
""".strip()
        else:
            prompt = f"""
你是 `brain`，正在为当前轮做第一次内部决策。

请先深入理解用户，再决定：
- 直接回复；或
- 调用 `central` 去完成事实核查、工具执行、多步求解。

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `intent`：你对用户当前意图的简要理解。
- `working_hypothesis`：你目前对问题的工作性判断。
- `task_action`：只能是 `create_task` 或 `none`。
- `task_reason`：为什么要直接回复，或为什么要启动 `central`。
- `final_decision`：如果启动 `central`，必须是 `continue`；如果直接回复，必须是 `answer`。
- `task_brief`：发给 `central` 的内部请求。只有在 `task_action=create_task` 时填写。
- `final_message`：只有在 `task_action=none` 时填写，必须使用与用户相同的语言。

硬性规则：
1. 如果 `task_action` = `create_task`：
   - `final_decision` 必须是 `continue`
   - `task_brief` 必须非空
   - `final_message` 必须是空字符串 `""`
2. 如果 `task_action` = `none`：
   - `final_decision` 必须是 `answer`
   - `task_brief` 必须是空字符串 `""`
   - `final_message` 必须非空
3. 不要遗漏任何字段。

标准结构：
{{
  "intent": "...",
  "working_hypothesis": "...",
  "task_action": "create_task|none",
  "task_reason": "...",
  "final_decision": "continue|answer",
  "task_brief": "...",
  "final_message": "..."
}}

启动 central 示例：
{{
  "intent": "用户希望解决一个需要工具和多步分析的问题",
  "working_hypothesis": "仅靠主脑当前上下文不足以保证结果准确，需要执行系统补齐事实",
  "task_action": "create_task",
  "task_reason": "需要调用工具并进行多步执行",
  "final_decision": "continue",
  "task_brief": "请检查当前问题需要哪些工具，完成分析并返回最终执行结果、风险和缺失信息。",
  "final_message": ""
}}

直接回复示例：
{{
  "intent": "用户在表达情绪并希望被承接",
  "working_hypothesis": "当前更适合由主脑直接回应，不需要执行系统介入",
  "task_action": "none",
  "task_reason": "这轮主要是理解和回应，不需要工具或事实核查",
  "final_decision": "answer",
  "task_brief": "",
  "final_message": "我在，先别急，你可以慢慢跟我说。"
}}

用户输入：{user_input}
""".strip()

        raw_text, metrics = await self._run_brain_task(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_task_summaries=None,
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
        brain_intent: str,
        brain_working_hypothesis: str,
        task_summary: str,
        task_status: str,
        task_missing: list[str],
        task_recommended_action: str,
        loop_count: int,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> BrainFinalizePacket:
        prompt = f"""
你是 `brain`，正在读取本轮 `central` 结果并做最终决策。

请综合：
- 你的初始判断
- 当前 `central` 返回结果
- 用户真实需求

然后只在以下三种决策中选择一种：
- `answer`：已经可以直接对用户回复
- `ask_user`：必须让用户补充信息
- `continue`：还需要继续让 `central` 往下执行

你必须只返回一个 JSON 对象，不能输出解释、前言、Markdown、代码块、补充说明。

字段说明：
- `final_decision`：只能是 `answer`、`ask_user`、`continue`。
- `final_message`：当决策为 `answer` 或 `ask_user` 时，要给用户看的话；必须使用与用户相同的语言。
- `task_brief`：当决策为 `continue` 时，发给 `central` 的下一条内部问题。

硬性规则：
1. 如果 `final_decision` = `continue`：
   - `task_brief` 必须非空
   - `final_message` 必须是空字符串 `""`
2. 如果 `final_decision` = `answer` 或 `ask_user`：
   - `final_message` 必须非空
   - `task_brief` 必须是空字符串 `""`
3. 不要遗漏任何字段。

标准结构：
{{
  "final_decision": "answer|ask_user|continue",
  "final_message": "...",
  "task_brief": "..."
}}

继续执行示例：
{{
  "final_decision": "continue",
  "final_message": "",
  "task_brief": "请继续处理尚未解决的部分，补齐关键缺失信息后返回最终结果。"
}}

要求用户补充示例：
{{
  "final_decision": "ask_user",
  "final_message": "我还缺一个关键信息：你希望我以哪个时间范围来查询？",
  "task_brief": ""
}}

直接回复示例：
{{
  "final_decision": "answer",
  "final_message": "我先把当前能确认的结论告诉你：这个方向是可行的。",
  "task_brief": ""
}}

用户输入：{user_input}
主脑意图：{brain_intent or '（空）'}
主脑工作假设：{self._compact_text(brain_working_hypothesis, limit=140) or '（空）'}
central 摘要：{self._compact_text(task_summary, limit=320) or '（空）'}
当前循环次数：{loop_count}
""".strip()

        raw_text, metrics = await self._run_brain_task(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_task_summaries=[task_summary] if task_summary else None,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
            query=(f"{user_input}\n{task_summary}".strip()),
            retrieval_focus=["user", "goal", "constraint", "tool", "skill"],
        )

        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_finalize(raw_text)
            if recovered is not None:
                recovered.update(metrics)
                return recovered
            fallback = self._fallback_finalize(
                task_status=task_status,
                task_analysis=task_summary,
                task_missing=task_missing,
                task_recommended_action=task_recommended_action,
            )
            fallback.update(metrics)
            return fallback

        normalized = self._normalize_finalize_payload(parsed)
        if normalized is None:
            fallback = self._fallback_finalize(
                task_status=task_status,
                task_analysis=task_summary,
                task_missing=task_missing,
                task_recommended_action=task_recommended_action,
            )
            fallback.update(metrics)
            return fallback
        normalized.update(metrics)
        return normalized

    def decide_paused_task(
        self,
        *,
        user_input: str,
        task: dict[str, Any],
        emotion: str,
    ) -> BrainControlPacket:
        action, reason = self._decide_paused_task_action(
            user_input=user_input,
            task=task,
            emotion=emotion,
        )
        if action == "resume_task":
            return {
                "action": "resume_task",
                "reason": reason,
                "task": dict(task or {}),
            }
        if action == "defer":
            return {
                "action": "defer",
                "reason": reason,
                "task": self._strip_resume_payload(task),
            }
        return {
            "action": "pause_task",
            "reason": reason,
            "final_decision": "answer",
            "message": self._build_paused_task_hold_message(
                user_input=user_input,
                task=task,
                reason=reason,
                emotion=emotion,
            ),
            "task": self._strip_resume_payload(task),
        }

    def control_after_deliberation(
        self,
        *,
        deliberation: BrainDeliberationPacket,
        emotion: str,
    ) -> BrainControlPacket:
        task_action = str(deliberation.get("task_action", "") or "").strip().lower()
        task_reason = str(deliberation.get("task_reason", "") or "").strip()
        if task_action == "create_task" or deliberation.get("needs_task"):
            return {
                "action": "create_task",
                "reason": task_reason or "brain_requested_task",
                "task_brief": str(
                    deliberation.get("task_brief", "")
                    or self._build_default_task_brief(
                        working_hypothesis=str(deliberation.get("working_hypothesis", "") or ""),
                        intent=str(deliberation.get("intent", "") or ""),
                    )
                ).strip(),
            }
        return {
            "action": "none",
            "reason": task_reason or "brain_answered_directly",
            "final_decision": "answer",
            "message": str(deliberation.get("final_message", "") or "").strip() or build_companion_prompt(emotion),
        }

    def control_after_finalize(
        self,
        *,
        finalize: BrainFinalizePacket,
        loop_count: int,
        max_loop_rounds: int,
        task_control_state: str,
        task_status: str,
        task_missing: list[str],
        task_analysis: str,
        task_risks: list[str],
    ) -> BrainControlPacket:
        decision = str(finalize.get("final_decision", "") or finalize.get("decision", "") or "answer").strip().lower()
        message = str(finalize.get("final_message", "") or finalize.get("message", "") or "").strip()
        task_brief = str(finalize.get("task_brief", "") or "").strip()

        if decision == "continue":
            if loop_count >= max_loop_rounds:
                forced_decision, forced_message = self._force_complete(
                    task_status=task_status,
                    task_missing=task_missing,
                    task_analysis=task_analysis,
                )
                return {
                    "action": "pause_task" if task_control_state == "paused" else "none",
                    "reason": "loop_limit_reached",
                    "final_decision": forced_decision,
                    "message": forced_message,
                }
            return {
                "action": "continue_task",
                "reason": "brain_requested_task_followup",
                "final_decision": "continue",
                "task_brief": task_brief
                or self._build_followup_task_brief(
                    task_risks=task_risks,
                    task_analysis=task_analysis,
                ),
            }

        if decision == "ask_user":
            return {
                "action": "pause_task" if task_control_state == "paused" else "none",
                "reason": "task_waiting_for_user_input"
                if task_control_state == "paused"
                else "brain_requested_user_input",
                "final_decision": "ask_user",
                "message": message or build_missing_info_prompt(task_missing),
            }

        return {
            "action": "none",
            "reason": "task_result_finalized"
            if task_control_state == "completed"
            else "brain_answered_from_task",
            "final_decision": "answer",
            "message": message or task_analysis or "我先给你一个当前能确认的结论，我们可以继续往下推进。",
        }

    def control_stop_task(
        self,
        *,
        cancelled_tasks: int,
        task: dict[str, Any] | None = None,
    ) -> BrainControlPacket:
        del task
        message = f"⏹ 已停止 {max(0, int(cancelled_tasks))} 个主任务"
        message += "。"
        return {
            "action": "cancel_task",
            "reason": "user_requested_stop",
            "final_decision": "answer",
            "message": message,
        }

    def build_task_delegation(
        self,
        *,
        action: str,
        user_input: str,
        task_brief: str,
        intent: str,
        working_hypothesis: str,
        session_id: str = "",
        loop_count: int = 0,
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = dict(task or {})
        goal = str(task_brief or "").strip() or self._build_default_task_brief(
            working_hypothesis=working_hypothesis,
            intent=intent,
        )
        bundle_query_parts = [goal, user_input, intent, working_hypothesis, str(task.get("summary", "") or "")]
        bundle = self.context.build_task_memory_bundle(
            query="\n".join(part for part in bundle_query_parts if str(part).strip()),
            limit=6,
        )
        missing = [str(item).strip() for item in list(task.get("missing", []) or []) if str(item).strip()]
        constraints = [
            "不要生成最终面向用户的回复。",
            "不要直接检索或写入长期记忆。",
            "只使用 brain 已经提供的相关记忆包。",
            "只返回最终执行结果，并保持事实紧凑、建议明确。",
            "遵守工作区、工具和审批边界。",
        ]
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "continue_task":
            constraints.append("只继续处理当前尚未解决的部分。")
        if normalized_action == "resume_task":
            constraints.append("基于提供的运行时上下文恢复已暂停的执行。")
        if missing:
            constraints.append("如果条件允许，优先解决当前已知的缺失输入。")

        delegation = {
            "goal": goal,
            "request": goal,
            "constraints": constraints,
            "relevant_task_memories": list(bundle.get("relevant_task_memories", []) or []),
            "relevant_tool_memories": list(bundle.get("relevant_tool_memories", []) or []),
            "skill_hints": list(bundle.get("skill_hints", []) or []),
            "success_criteria": [
                "返回一个 brain 可以直接吸收的最终执行结果。",
                "只有在确实影响完成度时，才列出阻塞风险或缺失输入。",
                "尽量减少内部往返，能在 central 内部收敛就不要再拆分。",
            ],
            "return_contract": {
                "mode": "final_only",
                "must_not": ["direct_user_reply", "memory_retrieval", "memory_write"],
            },
        }
        resume_payload = task.get("resume_payload")
        if resume_payload not in (None, "", [], {}):
            delegation["resume_payload"] = resume_payload
        return delegation

    def decide_deep_reflection(
        self,
        *,
        state: dict[str, Any],
        importance: float,
        task: dict[str, Any],
        turn_reflection: dict[str, Any],
    ) -> tuple[bool, str]:
        brain = state.get("brain")
        execution_review = (
            turn_reflection.get("execution_review")
            if isinstance(turn_reflection, dict) and isinstance(turn_reflection.get("execution_review"), dict)
            else {}
        )
        control_state = str(task.get("control_state", "") or "").strip().lower()
        status = str(task.get("status", "") or "").strip().lower()
        missing = [str(item).strip() for item in list(task.get("missing", []) or []) if str(item).strip()]
        pending_review = task.get("pending_review") if isinstance(task.get("pending_review"), dict) else {}
        effectiveness = str((execution_review or {}).get("effectiveness", "none") or "none").strip().lower()
        failure_reason = str((execution_review or {}).get("main_failure_reason", "") or "").strip()
        user_updates = [str(item).strip() for item in list(turn_reflection.get("user_updates", []) or []) if str(item).strip()]
        soul_updates = [str(item).strip() for item in list(turn_reflection.get("soul_updates", []) or []) if str(item).strip()]
        memory_candidates = list(turn_reflection.get("memory_candidates", []) or []) if isinstance(turn_reflection, dict) else []
        task_reason = str(getattr(brain, "task_reason", "") or "").strip() if brain is not None else ""

        if task.get("invoked") and (status in {"failed", "need_more"} or control_state == "paused"):
            return True, f"brain_task_followup:{control_state or status}"
        if task.get("invoked") and (missing or pending_review):
            return True, "brain_task_blocked_or_waiting_review"
        if task.get("invoked") and effectiveness in {"low", "medium"} and failure_reason:
            return True, f"brain_task_review:{failure_reason}"
        if importance >= 0.82 and (user_updates or soul_updates):
            return True, "brain_high_importance_identity_updates"
        if importance >= 0.82 and memory_candidates:
            return True, "brain_high_importance_memory_candidates"
        if task_reason in {
            "loop_limit_reached",
            "brain_requested_task_followup",
            "task_waiting_for_user_input",
        }:
            return True, f"brain_signal:{task_reason}"
        return False, ""

    async def _run_brain_task(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str,
        pad_state: tuple[float, float, float] | None,
        internal_task_summaries: list[str] | None,
        channel: str,
        chat_id: str,
        session_id: str,
        query: str,
        retrieval_focus: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        del channel, chat_id, session_id
        records = self.context.query_brain_memories(query=query, limit=8)
        messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_task_summaries=internal_task_summaries,
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
                "assistant_id": "emoticorebot-brain",
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
    ) -> BrainDeliberationPacket | None:
        if not isinstance(parsed, dict):
            return None

        intent = str(parsed.get("intent", "") or "").strip()
        working_hypothesis = str(parsed.get("working_hypothesis", "") or "").strip()
        task_action = str(parsed.get("task_action", "") or "").strip().lower()
        task_reason = str(parsed.get("task_reason", "") or "").strip()
        final_decision = str(parsed.get("final_decision", "") or "").strip().lower()
        needs_task = parsed.get("needs_task")
        if task_action not in {"none", "create_task", "answer", "start"}:
            if isinstance(needs_task, bool):
                task_action = "create_task" if needs_task else "none"
            else:
                return None
        if final_decision not in {"answer", "continue"}:
            final_decision = "continue" if task_action == "create_task" else "answer"

        task_brief = str(parsed.get("task_brief", "") or "").strip()
        final_message = str(parsed.get("final_message", "") or "").strip()

        if task_action in {"start"}:
            task_action = "create_task"
        if task_action == "answer":
            task_action = "none"
        if task_action == "create_task":
            final_decision = "continue"
        else:
            final_decision = "answer"

        if task_action == "create_task" and not task_brief:
            task_brief = working_hypothesis or intent
        if task_action == "none" and not final_message:
            return None

        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "task_action": task_action,
            "task_reason": task_reason,
            "final_decision": final_decision,
            "needs_task": task_action == "create_task",
            "task_brief": task_brief if task_action == "create_task" else "",
            "final_message": "" if task_action == "create_task" else final_message,
        }

    @classmethod
    def _normalize_finalize_payload(cls, parsed: dict[str, Any]) -> BrainFinalizePacket | None:
        if not isinstance(parsed, dict):
            return None

        decision = str(parsed.get("final_decision", "") or parsed.get("decision", "") or "").strip().lower()
        if decision not in {"answer", "ask_user", "continue"}:
            return None

        message = str(parsed.get("final_message", "") or parsed.get("message", "") or "").strip()
        task_brief = str(parsed.get("task_brief", "") or "").strip()
        if decision == "continue" and not task_brief:
            task_brief = message
        if decision != "continue":
            task_brief = ""
        if decision in {"answer", "ask_user"} and not message:
            return None

        return {
            "final_decision": decision,
            "final_message": message,
            "decision": decision,
            "message": message,
            "task_brief": task_brief,
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
    def _recover_deliberation(cls, raw: str) -> BrainDeliberationPacket | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        task_action = cls._extract_json_string_field(cleaned, "task_action").lower()
        task_reason = cls._extract_json_string_field(cleaned, "task_reason")
        final_decision = cls._extract_json_string_field(cleaned, "final_decision").lower()
        needs_task = cls._extract_json_bool_field(cleaned, "needs_task")
        intent = cls._extract_json_string_field(cleaned, "intent")
        working_hypothesis = cls._extract_json_string_field(cleaned, "working_hypothesis")
        task_brief = cls._extract_json_string_field(cleaned, "task_brief")
        final_message = cls._extract_json_string_field(cleaned, "final_message")
        if task_action in {"start", "create_task"} and task_brief:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "task_action": "create_task",
                "task_reason": task_reason,
                "final_decision": "continue",
                "needs_task": True,
                "task_brief": task_brief,
                "final_message": "",
            }
        if task_action in {"answer", "none"} and final_message:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "task_action": "none",
                "task_reason": task_reason,
                "final_decision": "answer",
                "needs_task": False,
                "task_brief": "",
                "final_message": final_message,
            }
        if needs_task is True and task_brief:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "needs_task": True,
                "task_brief": task_brief,
                "final_message": "",
            }
        if needs_task is False and final_message:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "needs_task": False,
                "task_brief": "",
                "final_message": final_message,
                "task_action": "none",
            }
        return None

    @classmethod
    def _recover_finalize(cls, raw: str) -> BrainFinalizePacket | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        decision = cls._extract_json_string_field(cleaned, "final_decision") or cls._extract_json_string_field(cleaned, "decision") or "answer"
        message = cls._extract_json_string_field(cleaned, "final_message") or cls._extract_json_string_field(cleaned, "message")
        task_brief = cls._extract_json_string_field(cleaned, "task_brief")
        if decision not in {"answer", "ask_user", "continue"}:
            decision = "answer"
        if decision == "continue":
            if not task_brief:
                return None
            return {
                "final_decision": decision,
                "final_message": "",
                "decision": decision,
                "message": "",
                "task_brief": task_brief,
            }
        if not message:
            return None
        return {
            "final_decision": decision,
            "final_message": message,
            "decision": decision,
            "message": message,
            "task_brief": "",
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
    ) -> BrainDeliberationPacket:
        if cls._looks_task_like(user_input):
            return {
                "intent": "用户需要事实分析、执行帮助，或更强的问题求解。",
                "working_hypothesis": "在给出最终表达前，需要先调用 central 补齐事实与执行判断。",
                "task_action": "create_task",
                "task_reason": "brain_requested_task",
                "final_decision": "continue",
                "needs_task": True,
                "task_brief": "请分析用户请求需要的事实、工具、风险与最合适的下一步。",
                "final_message": "",
            }
        return {
                "intent": "用户当前更需要陪伴式回应或轻量交流。",
                "working_hypothesis": "这一轮无需调用 central。",
                "task_action": "none",
                "task_reason": "brain_answered_directly",
                "final_decision": "answer",
                "needs_task": False,
            "task_brief": "",
            "final_message": build_companion_prompt(emotion),
        }

    @classmethod
    def _fallback_finalize(
        cls,
        *,
        task_status: str,
        task_analysis: str,
        task_missing: list[str],
        task_recommended_action: str,
    ) -> BrainFinalizePacket:
        if (
            task_missing
            or task_status == "need_more"
            or task_recommended_action == "ask_user"
        ):
            return {
                "final_decision": "ask_user",
                "final_message": build_missing_info_prompt(task_missing),
                "decision": "ask_user",
                "message": build_missing_info_prompt(task_missing),
                "task_brief": "",
            }
        if task_recommended_action == "continue_task":
            return {
                "final_decision": "continue",
                "final_message": "",
                "decision": "continue",
                "message": "",
                "task_brief": "请补上最关键的证据缺口、主要风险，以及最稳妥的下一步。",
            }
        return {
            "final_decision": "answer",
            "final_message": task_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
            "decision": "answer",
            "message": task_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
            "task_brief": "",
        }

    @staticmethod
    def _build_default_task_brief(*, working_hypothesis: str, intent: str) -> str:
        if working_hypothesis:
            return (
                "Analyze the current working hypothesis, identify evidence, risks, "
                f"and the best next action: {working_hypothesis}"
            )
        if intent:
            return f"Analyze this user intent, identify evidence, risks, and the best next action: {intent}"
        return "Analyze the current internal question and return evidence, risks, and the best next action."

    @staticmethod
    def _build_followup_task_brief(*, task_risks: list[str], task_analysis: str) -> str:
        if task_risks:
            risk_text = "; ".join(str(item).strip() for item in task_risks[:2] if str(item).strip())
            if risk_text:
                return f"Focus on these key risks and produce a more robust next step: {risk_text}"
        if task_analysis:
            return f"Strengthen the weakest part of this analysis and make the next action clearer: {task_analysis}"
        return "Fill the most important evidence gaps and provide the next action."

    @staticmethod
    def _force_complete(*, task_status: str, task_missing: list[str], task_analysis: str) -> tuple[str, str]:
        if task_status == "need_more" or task_missing:
            return "ask_user", build_missing_info_prompt(task_missing)
        if task_analysis:
            return "answer", task_analysis
        return "answer", "我先给你一个阶段性结论，不过还需要更多信息才能更稳。"

    @staticmethod
    def _strip_resume_payload(task: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(task or {})
        cleaned.pop("resume_payload", None)
        return cleaned

    @classmethod
    def _decide_paused_task_action(
        cls,
        *,
        user_input: str,
        task: dict[str, Any],
        emotion: str,
    ) -> tuple[str, str]:
        text = str(user_input or "").strip()
        resume_payload = task.get("resume_payload")
        pending_review = dict(task.get("pending_review", {}) or {})
        missing = [str(item).strip() for item in (task.get("missing", []) or []) if str(item).strip()]

        if cls._looks_like_pause_request(text):
            return "pause_task", "user_requested_pause"
        if resume_payload not in (None, "", [], {}):
            if pending_review:
                return "resume_task", "user_responded_to_pending_review"
            if missing:
                return "resume_task", "user_provided_missing_information"
            return "resume_task", "user_requested_resume"
        if cls._looks_like_resume_request(text):
            if missing:
                return "resume_task", "user_requested_resume_for_missing_information"
            if not pending_review:
                return "resume_task", "user_requested_resume"
        if cls._looks_like_companionship_or_explanation(text, emotion=emotion):
            return "pause_task", "brain_prioritized_companionship_or_explanation"
        if cls._looks_like_priority_switch(text):
            return "defer", "user_switched_priority"
        if missing:
            if cls._looks_like_new_task_input(text):
                return "defer", "user_started_new_topic_while_task_paused"
            if text:
                return "resume_task", "user_provided_missing_information"
        if pending_review and text:
            return "defer", "user_started_new_topic_while_review_paused"
        if text:
            return "defer", "paused_task_left_on_hold"
        return "pause_task", "paused_task_waiting"

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
    def _build_paused_task_hold_message(
        *,
        user_input: str,
        task: dict[str, Any],
        reason: str,
        emotion: str,
    ) -> str:
        del task, emotion
        if reason == "user_requested_pause":
            return "好，我先把刚才的执行保持暂停，不继续往下跑。你想恢复时直接跟我说‘继续’就行。"
        if reason == "brain_prioritized_companionship_or_explanation":
            if any(token in str(user_input or "").lower() for token in ("什么意思", "解释", "为什么", "怎么回事", "explain", "why")):
                return "好，我先不继续跑刚才的执行。你现在更想让我先解释当前进展，还是你要先补充信息继续？"
            return "好，我先不继续跑刚才的执行，先陪你把现在这部分理顺。你可以直接告诉我，此刻最想先处理的是哪一点。"
        if reason == "user_switched_priority":
            return "好，我先把刚才的执行挂起，不往下推进。你现在更急的这件事，我们先把重点说清楚。"
        return "好，我先保持当前执行暂停。你想恢复时告诉我‘继续’，或直接补充新的信息也可以。"


__all__ = ["BrainService"]


