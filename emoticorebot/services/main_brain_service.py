"""Main-brain service for deliberation and final user-facing decisions."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.core.state import MainBrainDeliberationPacket, MainBrainFinalizePacket
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
        system_prompt = self.context.build_main_brain_system_prompt(query=prompt)
        if create_deep_agent is not None:
            try:
                raw_result = await self._invoke_deep_agent(
                    system_prompt=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                    channel=channel,
                    chat_id=chat_id,
                    session_id=session_id,
                )
                text = self._extract_text(raw_result).strip()
                if text:
                    return text
            except Exception:
                pass

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
            prompt = (
                "You are doing the first internal main-brain pass.\n"
                "This turn looks like companionship or lightweight conversation, so executor help is not needed.\n"
                "If you write final_message, use the same language as the user.\n"
                "Return only one JSON object.\n"
                '{"intent":"...","working_hypothesis":"...","need_executor":false,'
                '"question_to_executor":"","final_message":"..."}\n\n'
                f"User input: {user_input}\n"
            )
        else:
            prompt = (
                "You are doing the first internal main-brain pass.\n"
                "Understand the user deeply, then decide whether executor help is needed.\n"
                "If executor help is needed, compress it into one clear internal request.\n"
                "If you write final_message, use the same language as the user.\n"
                "Return only one JSON object.\n"
                '{"intent":"...","working_hypothesis":"...","need_executor":true,'
                '"question_to_executor":"...","final_message":""}\n\n'
                f"User input: {user_input}\n"
            )

        raw_text, metrics = await self._run_main_brain_task(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_executor_summaries=None,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
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
        executor_missing_params: list[str],
        executor_recommended_action: str,
        loop_count: int,
        channel: str = "",
        chat_id: str = "",
        session_id: str = "",
    ) -> MainBrainFinalizePacket:
        prompt = (
            "You are doing the second internal main-brain pass.\n"
            "Combine your first judgment with the executor result.\n"
            "Choose one decision: answer, ask_user, or continue_deliberation.\n"
            "If you write message, use the same language as the user.\n"
            "Return only one JSON object.\n"
            '{"decision":"answer|ask_user|continue_deliberation","message":"...",'
            '"question_to_executor":"if continuing, provide the next internal question; otherwise empty"}\n\n'
            f"User input: {user_input}\n"
            f"Main brain intent: {main_brain_intent or '(empty)'}\n"
            f"Main brain working hypothesis: {self._compact_text(main_brain_working_hypothesis, limit=140) or '(empty)'}\n"
            f"Executor summary: {self._compact_text(executor_summary, limit=320) or '(empty)'}\n"
            f"Loop count: {loop_count}\n"
        )

        raw_text, metrics = await self._run_main_brain_task(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_executor_summaries=[executor_summary] if executor_summary else None,
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
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
                executor_missing_params=executor_missing_params,
                executor_recommended_action=executor_recommended_action,
            )
            fallback.update(metrics)
            return fallback

        normalized = self._normalize_finalize_payload(parsed)
        if normalized is None:
            fallback = self._fallback_finalize(
                executor_status=executor_status,
                executor_analysis=executor_summary,
                executor_missing_params=executor_missing_params,
                executor_recommended_action=executor_recommended_action,
            )
            fallback.update(metrics)
            return fallback
        normalized.update(metrics)
        return normalized

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
    ) -> tuple[str, dict[str, Any]]:
        messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_executor_summaries=internal_executor_summaries,
        )
        system_prompt = str(messages[0].get("content", "") or "") if messages else ""
        payload_messages = messages[1:] if len(messages) > 1 else [{"role": "user", "content": current_message}]

        if create_deep_agent is not None:
            try:
                raw_result = await self._invoke_deep_agent(
                    system_prompt=system_prompt,
                    messages=payload_messages,
                    channel=channel,
                    chat_id=chat_id,
                    session_id=session_id,
                )
                return self._extract_text(raw_result), self._extract_result_metrics(raw_result)
            except Exception:
                pass

        response = await self.brain_llm.ainvoke(messages)
        return extract_message_text(response), extract_message_metrics(response)

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
            base = f"{channel_text}:{chat_text}" if channel_text or chat_text else "main_brain"
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
        return f"main_brain_{digest}"

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

        need_executor = parsed.get("need_executor")
        if not isinstance(need_executor, bool):
            return None

        intent = str(parsed.get("intent", "") or "").strip()
        working_hypothesis = str(parsed.get("working_hypothesis", "") or "").strip()
        question_to_executor = str(parsed.get("question_to_executor", "") or "").strip()
        final_message = str(parsed.get("final_message", "") or "").strip()

        if need_executor and not question_to_executor:
            question_to_executor = working_hypothesis or intent
        if not need_executor and not final_message:
            return None

        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "need_executor": need_executor,
            "question_to_executor": question_to_executor if need_executor else "",
            "final_message": "" if need_executor else final_message,
        }

    @classmethod
    def _normalize_finalize_payload(cls, parsed: dict[str, Any]) -> MainBrainFinalizePacket | None:
        if not isinstance(parsed, dict):
            return None

        decision = str(parsed.get("decision", "") or "").strip().lower()
        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            return None

        message = str(parsed.get("message", "") or "").strip()
        question_to_executor = str(parsed.get("question_to_executor", "") or "").strip()
        if decision == "continue_deliberation" and not question_to_executor:
            question_to_executor = message
        if decision != "continue_deliberation":
            question_to_executor = ""
        if decision in {"answer", "ask_user"} and not message:
            return None

        return {
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
        need_executor = cls._extract_json_bool_field(cleaned, "need_executor")
        intent = cls._extract_json_string_field(cleaned, "intent")
        working_hypothesis = cls._extract_json_string_field(cleaned, "working_hypothesis")
        question_to_executor = cls._extract_json_string_field(cleaned, "question_to_executor")
        final_message = cls._extract_json_string_field(cleaned, "final_message")
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
        decision = cls._extract_json_string_field(cleaned, "decision") or "answer"
        message = cls._extract_json_string_field(cleaned, "message")
        question_to_executor = cls._extract_json_string_field(cleaned, "question_to_executor")
        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            decision = "answer"
        if decision == "continue_deliberation":
            if not question_to_executor:
                return None
            return {
                "decision": decision,
                "message": "",
                "question_to_executor": question_to_executor,
            }
        if not message:
            return None
        return {
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
                "need_executor": True,
                "question_to_executor": "请分析用户请求需要的事实、工具、风险与最合适的下一步。",
                "final_message": "",
            }
        return {
            "intent": "用户当前更需要陪伴式回应或轻量交流。",
            "working_hypothesis": "这一轮无需调用 executor。",
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
        executor_missing_params: list[str],
        executor_recommended_action: str,
    ) -> MainBrainFinalizePacket:
        if (
            executor_missing_params
            or executor_status == "needs_input"
            or executor_recommended_action == "ask_user"
        ):
            return {
                "decision": "ask_user",
                "message": build_missing_info_prompt(executor_missing_params),
                "question_to_executor": "",
            }
        if executor_status == "uncertain" or executor_recommended_action == "continue_deliberation":
            return {
                "decision": "continue_deliberation",
                "message": "",
                "question_to_executor": "请补上最关键的证据缺口、主要风险，以及最稳妥的下一步。",
            }
        return {
            "decision": "answer",
            "message": executor_analysis or "我已经把当前思路理顺了，我们可以顺着这个继续。",
            "question_to_executor": "",
        }


__all__ = ["MainBrainService"]
