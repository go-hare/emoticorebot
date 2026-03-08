"""EQ Service - EQ 主导的内部讨论与最终表达。"""

from __future__ import annotations

import json
import re
from typing import Any

from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.core.state import EQDeliberationPacket, EQFinalizePacket
from emoticorebot.utils.llm_utils import extract_message_metrics, extract_message_text


class EQService:
    """EQ 主导服务。

    - deliberate: 用户输入后的第一轮主导判断
    - finalize:   读取 IQ 分析包后的最终决策与对外表达
    """

    def __init__(self, eq_llm, context_builder: ContextBuilder):
        self.eq_llm = eq_llm
        self.context = context_builder

    @staticmethod
    def _compact_text(text: Any, limit: int = 160) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    async def generate_proactive(self, prompt: str) -> str:
        system = self.context.build_eq_system_prompt()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        resp = await self.eq_llm.ainvoke(messages)
        return extract_message_text(resp).strip()

    async def deliberate(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
    ) -> EQDeliberationPacket:
        lightweight_chat = not self._looks_task_like(user_input)
        if lightweight_chat:
            prompt = (
                "你正在进行第一轮内部主导判断。\n"
                "这更像普通闲聊、问候或轻陪伴，不需要征询 IQ。\n"
                "请直接给出一句贴近 SOUL.md 的自然回复，短一点、有温度。\n"
                "必须只输出一个 JSON 对象，不要输出任何额外文本。\n"
                "JSON 格式：\n"
                '{"intent":"...","working_hypothesis":"...","need_iq":false,'
                '"question_to_iq":"","final_message":"..."}\n\n'
                f"用户输入：{user_input}\n"
            )
        else:
            prompt = (
                "你正在进行第一轮内部主导判断。\n"
                "请先理解用户真正需要什么，再决定是否征询 IQ。\n"
                "如果需要 IQ，就把问题压缩成一句清晰、可执行、通用的话。\n"
                "必须只输出一个 JSON 对象，不要输出任何额外文本。\n"
                "JSON 格式：\n"
                '{"intent":"...","working_hypothesis":"...","need_iq":true,'
                '"question_to_iq":"...","final_message":""}\n\n'
                f"用户输入：{user_input}\n"
            )

        messages = self.context.build_messages(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        )
        resp = await self.eq_llm.ainvoke(messages)
        llm_metrics = extract_message_metrics(resp)
        raw_text = extract_message_text(resp)
        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_deliberation(raw_text)
            if recovered is not None:
                recovered.update(llm_metrics)
                return recovered
            fallback = self._fallback_deliberation(user_input=user_input, emotion=emotion)
            fallback.update(llm_metrics)
            return fallback

        parsed = self._normalize_deliberation_payload(parsed)
        if parsed is None:
            fallback = self._fallback_deliberation(user_input=user_input, emotion=emotion)
            fallback.update(llm_metrics)
            return fallback
        parsed.update(llm_metrics)
        return parsed

    async def finalize(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        eq_intent: str,
        eq_working_hypothesis: str,
        iq_summary: str,
        iq_status: str,
        iq_missing_params: list[str],
        iq_recommended_action: str,
        discussion_count: int,
    ) -> EQFinalizePacket:
        prompt = (
            "你正在进行第二轮内部综合判断。\n"
            "请综合你自己的初判与 IQ 的分析，决定是否直接答用户、向用户追问，或继续向 IQ 发起一轮内部讨论。\n"
            "必须只输出一个 JSON 对象，不要输出任何额外文本。\n"
            "JSON 格式：\n"
            '{"decision":"answer|ask_user|continue_deliberation","message":"给用户的话；若继续内部讨论可为空",'
            '"question_to_iq":"若继续讨论则填写问题，否则空字符串"}\n\n'
            f"用户输入：{user_input}\n"
            f"EQ intent：{eq_intent or '(空)'}\n"
            f"EQ working_hypothesis：{self._compact_text(eq_working_hypothesis, limit=140) or '(空)'}\n"
            f"IQ summary：{self._compact_text(iq_summary, limit=320) or '(空)'}\n"
            f"已讨论轮数：{discussion_count}\n"
        )

        messages = self.context.build_messages(
            history=history,
            current_message=prompt,
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
        )
        resp = await self.eq_llm.ainvoke(messages)
        llm_metrics = extract_message_metrics(resp)
        raw_text = extract_message_text(resp)
        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_finalize(raw_text)
            if recovered is not None:
                recovered.update(llm_metrics)
                return recovered
            fallback = self._fallback_finalize(
                iq_status=iq_status,
                iq_analysis=iq_summary,
                iq_missing_params=iq_missing_params,
                iq_recommended_action=iq_recommended_action,
            )
            fallback.update(llm_metrics)
            return fallback

        parsed = self._normalize_finalize_payload(parsed, user_input=user_input)
        if parsed is None:
            fallback = self._fallback_finalize(
                iq_status=iq_status,
                iq_analysis=iq_summary,
                iq_missing_params=iq_missing_params,
                iq_recommended_action=iq_recommended_action,
            )
            fallback.update(llm_metrics)
            return fallback
        parsed.update(llm_metrics)
        return parsed

    @classmethod
    def _normalize_deliberation_payload(cls, parsed: dict[str, Any]) -> EQDeliberationPacket | None:
        if not isinstance(parsed, dict):
            return None

        normalized = dict(parsed)
        legacy_decision = cls._map_legacy_eq_decision(parsed.get("decision"))

        if legacy_decision == "answer":
            normalized["need_iq"] = False
            normalized.setdefault("final_message", str(parsed.get("message", "") or "").strip())
        elif legacy_decision in {"continue_deliberation", "ask_user"}:
            normalized["need_iq"] = True
            normalized.setdefault("question_to_iq", str(parsed.get("message", "") or "").strip())

        need_iq = normalized.get("need_iq")
        if not isinstance(need_iq, bool):
            return None

        intent = str(normalized.get("intent", "") or "").strip()
        working_hypothesis = str(normalized.get("working_hypothesis", "") or "").strip()
        question_to_iq = str(normalized.get("question_to_iq", "") or "").strip()
        final_message = str(normalized.get("final_message", "") or "").strip()

        if need_iq and not question_to_iq:
            question_to_iq = working_hypothesis or intent
        if not need_iq and not final_message:
            return None

        return {
            "intent": intent,
            "working_hypothesis": working_hypothesis,
            "need_iq": need_iq,
            "question_to_iq": question_to_iq if need_iq else "",
            "final_message": "" if need_iq else final_message,
        }

    @classmethod
    def _normalize_finalize_payload(cls, parsed: dict[str, Any], *, user_input: str) -> EQFinalizePacket | None:
        if not isinstance(parsed, dict):
            return None

        normalized = dict(parsed)
        decision = str(normalized.get("decision", "") or "").strip().lower()

        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            decision = cls._map_legacy_eq_decision(decision)
        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            return None

        message = str(normalized.get("message", "") or "").strip()
        question_to_iq = str(normalized.get("question_to_iq", "") or "").strip()
        if decision == "continue_deliberation" and not question_to_iq:
            question_to_iq = message
        if decision != "continue_deliberation":
            question_to_iq = ""
        if decision in {"answer", "ask_user"} and not message:
            return None

        return {
            "decision": decision,
            "message": message,
            "question_to_iq": question_to_iq,
        }

    @staticmethod
    def _map_legacy_eq_decision(value: Any) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "accept": "answer",
            "delegate": "continue_deliberation",
            "retry": "continue_deliberation",
            "ask": "ask_user",
            "answer": "answer",
            "ask_user": "ask_user",
            "continue_deliberation": "continue_deliberation",
        }
        return mapping.get(text, "")

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
            return value.replace('\\n', '\n').replace('\\"', '"').strip()

    @staticmethod
    def _extract_json_bool_field(raw: str, field: str) -> bool | None:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*(true|false)', raw, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).lower() == "true"

    @classmethod
    def _recover_deliberation(cls, raw: str) -> EQDeliberationPacket | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        need_iq = cls._extract_json_bool_field(cleaned, "need_iq")
        intent = cls._extract_json_string_field(cleaned, "intent")
        working_hypothesis = cls._extract_json_string_field(cleaned, "working_hypothesis")
        question_to_iq = cls._extract_json_string_field(cleaned, "question_to_iq")
        final_message = cls._extract_json_string_field(cleaned, "final_message")
        if need_iq is True and question_to_iq:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "need_iq": True,
                "question_to_iq": question_to_iq,
                "final_message": "",
            }
        if need_iq is False and final_message:
            return {
                "intent": intent,
                "working_hypothesis": working_hypothesis,
                "need_iq": False,
                "question_to_iq": "",
                "final_message": final_message,
            }
        return None

    @classmethod
    def _recover_finalize(cls, raw: str) -> EQFinalizePacket | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        decision = cls._extract_json_string_field(cleaned, "decision") or "answer"
        message = cls._extract_json_string_field(cleaned, "message")
        question_to_iq = cls._extract_json_string_field(cleaned, "question_to_iq")
        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            decision = "answer"
        if decision == "continue_deliberation":
            if not question_to_iq:
                return None
            return {
                "decision": decision,
                "message": "",
                "question_to_iq": question_to_iq,
            }
        if not message:
            return None
        return {
            "decision": decision,
            "message": message,
            "question_to_iq": "",
        }

    @staticmethod
    def _looks_task_like(user_input: str) -> bool:
        text = (user_input or "").lower()
        keywords = [
            "帮我",
            "请",
            "查",
            "搜索",
            "订",
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
            "?",
            "？",
        ]
        return any(keyword in text for keyword in keywords)

    @classmethod
    def _fallback_deliberation(
        cls,
        *,
        user_input: str,
        emotion: str = "平静",
    ) -> EQDeliberationPacket:
        if cls._looks_task_like(user_input):
            return {
                "intent": "用户需要事实分析或任务帮助",
                "working_hypothesis": "应先征询 IQ 获取事实与可执行性判断",
                "need_iq": True,
                "question_to_iq": "请分析用户请求的可执行性、需要的事实或工具、风险与下一步建议。",
                "final_message": "",
            }
        return {
            "intent": "用户更需要陪伴或轻量回应",
            "working_hypothesis": "此时无需征询 IQ",
            "need_iq": False,
            "question_to_iq": "",
            "final_message": build_companion_prompt(emotion),
        }

    @classmethod
    def _fallback_finalize(
        cls,
        *,
        iq_status: str,
        iq_analysis: str,
        iq_missing_params: list[str],
        iq_recommended_action: str,
    ) -> EQFinalizePacket:
        if iq_missing_params or iq_status == "needs_input" or iq_recommended_action == "ask_user":
            return {
                "decision": "ask_user",
                "message": build_missing_info_prompt(iq_missing_params),
                "question_to_iq": "",
            }
        if iq_status == "uncertain" or iq_recommended_action == "continue_deliberation":
            return {
                "decision": "continue_deliberation",
                "message": "",
                "question_to_iq": "请补充最关键的证据、风险和下一步建议，帮助我做最终判断。",
            }
        return {
            "decision": "answer",
            "message": iq_analysis or "嗯，我把思路理顺了。你要是愿意，我们就顺着这个继续。",
            "question_to_iq": "",
        }


__all__ = ["EQService"]
