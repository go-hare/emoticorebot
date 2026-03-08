"""EQ Service - EQ 主导的内部讨论与最终表达。"""

from __future__ import annotations

import json
import re
from typing import Any
from emoticorebot.core.context import ContextBuilder
from emoticorebot.core.reply_utils import build_companion_prompt, build_missing_info_prompt
from emoticorebot.utils.llm_utils import extract_message_text
from time import time

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

    @classmethod
    def _summarize_list(cls, items: list[Any], *, item_limit: int = 3, text_limit: int = 80) -> str:
        cleaned = [cls._compact_text(item, limit=text_limit) for item in items if str(item or "").strip()]
        cleaned = [item for item in cleaned if item]
        if not cleaned:
            return "(空)"
        return " | ".join(cleaned[:item_limit])

    @classmethod
    def _summarize_options(cls, options: list[dict[str, Any]], *, item_limit: int = 2) -> str:
        if not isinstance(options, list):
            return "(空)"
        parts: list[str] = []
        for option in options[:item_limit]:
            if not isinstance(option, dict):
                continue
            name = cls._compact_text(option.get("name", ""), limit=24)
            description = cls._compact_text(option.get("description", ""), limit=60)
            tradeoff = cls._compact_text(option.get("tradeoff", ""), limit=40)
            bit = name or "option"
            if description:
                bit += f": {description}"
            if tradeoff:
                bit += f"（取舍: {tradeoff}）"
            parts.append(bit)
        return " | ".join(parts) if parts else "(空)"

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
        pending_task: dict[str, Any] | None,
        current_task: dict[str, Any] | None,
        internal_iq_summaries: list[str] | None = None,
    ) -> dict[str, Any]:
        lightweight_chat = not pending_task and not self._looks_task_like(user_input)
        if lightweight_chat:
            prompt = (
                "你正在进行第一轮内部主导判断。\n"
                "这更像普通闲聊、问候或轻陪伴，不需要征询 IQ。\n"
                "你还需要判断这条输入是否属于已有任务延续、新任务，或根本不是任务。\n"
                "请直接给出一句贴近 SOUL.md 的自然回复，短一点、有温度、有一点灵气。\n"
                "必须只输出一个 JSON 对象，不要输出任何额外文本。\n"
                "不要生成 selected_experts、expert_questions、question_to_iq 的冗长内容。\n"
                "JSON 格式：\n"
                '{"intent":"...","emotional_goal":"...","working_hypothesis":"...","need_iq":false,'
                '"question_to_iq":"","selected_experts":[],"expert_questions":{},'
                '"task_continuity":"none|continue|new","task_label":"...",'
                '"final_decision":"answer","final_message":"...","reason":"..."}\n\n'
                f"用户输入：{user_input}\n"
            )
        else:
            prompt = (
                "你正在进行第一轮内部主导判断。\n"
                "请先理解用户真正需要什么，再决定是否征询 IQ。\n"
                "你还必须判断：这条输入是在继续当前/旧任务，还是已经切到一个新任务。\n"
                "专家选择规则：\n"
                "1. 默认只选 ActionExpert\n"
                "2. 只有涉及历史承接/续聊/待续任务时才加 MemoryOverlay\n"
                "3. 只有涉及高风险动作、强事实判断、或你预期主专家置信度偏低时才加 RiskOverlay\n"
                "4. 为了轻量化，最多选择 2 个专家\n"
                "5. 只要用了 Overlay，通常仍应保留 ActionExpert 作为主专家\n"
                "你必须只输出一个 JSON 对象，不要输出任何额外文本。\n"
                "JSON 格式：\n"
                '{"intent":"...","emotional_goal":"...","working_hypothesis":"...","need_iq":true,"question_to_iq":"...",'
                '"selected_experts":["ActionExpert"],"expert_questions":{"ActionExpert":"..."},'
                '"task_continuity":"none|continue|new","task_label":"...",'
                '"final_decision":"","final_message":"","reason":"..."}\n\n'
                f"用户输入：{user_input}\n"
            )
        if pending_task:
            prompt += (
                "\n[Pending Task]\n"
                f"待续任务：{self._compact_text(pending_task.get('task', ''), limit=120) or '(空)'}\n"
                f"待续任务ID：{str(pending_task.get('task_id', '') or '').strip() or '(空)'}\n"
                f"之前缺失参数：{', '.join(str(x).strip() for x in pending_task.get('missing_params', []) if str(x).strip()) or '(无)'}\n"
                f"之前追问：{self._compact_text(pending_task.get('prompt', ''), limit=120) or '(空)'}\n"
                "如果用户当前输入像是在补充这些信息，优先征询 IQ 继续原任务。\n"
            )
        if current_task:
            prompt += (
                "\n[Current Task Anchor]\n"
                f"当前任务ID：{str(current_task.get('task_id', '') or '').strip() or '(空)'}\n"
                f"当前任务标签：{str(current_task.get('task_label', '') or '').strip() or '(空)'}\n"
                f"最近更新时间：{str(current_task.get('updated_at', '') or '').strip() or '(空)'}\n"
            )

        messages = self.context.build_messages(
            history=history,
            current_message=prompt,
            mode="eq",
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_iq_summaries=internal_iq_summaries,
        )
        start = time()
        resp = await self.eq_llm.ainvoke(messages)
        end = time()
        print("deliberate-messages:",messages)
        print("deliberate-resp:",resp)
        print(f"deliberate-llm-时间：{end - start}秒")
        raw_text = extract_message_text(resp)
        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_deliberation(raw_text)
            if recovered is not None:
                recovered.setdefault("selected_experts", [])
                recovered.setdefault("expert_questions", {})
                recovered.setdefault("question_to_iq", "")
                recovered.setdefault("reason", "recovered_partial_json")
                return recovered
            return self._fallback_deliberation(user_input=user_input, pending_task=pending_task, emotion=emotion)

        parsed = self._normalize_deliberation_payload(parsed)
        if parsed is None:
            return self._fallback_deliberation(user_input=user_input, pending_task=pending_task, emotion=emotion)

        need_iq = bool(parsed.get("need_iq", False))
        if need_iq:
            selected_experts, expert_questions = self._normalize_expert_plan(
                selected_experts=parsed.get("selected_experts"),
                expert_questions=parsed.get("expert_questions"),
                need_iq=need_iq,
                question_to_iq=str(parsed.get("question_to_iq", "") or "").strip(),
                pending_task=pending_task,
                user_input=user_input,
            )
        else:
            selected_experts, expert_questions = [], {}
        return {
            "intent": str(parsed.get("intent", "") or "").strip(),
            "emotional_goal": str(parsed.get("emotional_goal", "") or "").strip(),
            "working_hypothesis": str(parsed.get("working_hypothesis", "") or "").strip(),
            "need_iq": need_iq,
            "question_to_iq": str(parsed.get("question_to_iq", "") or "").strip(),
            "selected_experts": selected_experts,
            "expert_questions": expert_questions,
            "final_decision": str(parsed.get("final_decision", "") or "").strip().lower(),
            "final_message": str(parsed.get("final_message", "") or "").strip(),
            "reason": str(parsed.get("reason", "") or "").strip(),
        }

    async def finalize(
        self,
        *,
        user_input: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        pending_task: dict[str, Any] | None,
        current_task: dict[str, Any] | None,
        internal_iq_summaries: list[str] | None,
        eq_intent: str,
        eq_emotional_goal: str,
        eq_working_hypothesis: str,
        iq_summary: str,
        iq_status: str,
        iq_missing_params: list[str],
        iq_recommended_action: str,
        iq_selected_experts: list[str],
        discussion_count: int,
    ) -> dict[str, Any]:
        prompt = (
            "你正在进行第二轮内部综合判断。\n"
            "请综合你自己的初判与 IQ 的分析，决定是否直接答用户、向用户追问，或继续向 IQ 发起一轮内部讨论。\n"
            "你也是最终仲裁者：如果本轮已有专家包，你需要明确写出采纳了哪些专家、压过了哪些专家，以及一句最短裁决摘要。\n"
            "你还必须明确裁定：这轮对话属于继续旧任务、切换成新任务，还是非任务型互动。\n"
            "专家选择规则：\n"
            "1. 默认延续或收缩到 ActionExpert\n"
            "2. 只有当当前问题明显与历史承接相关时才保留/追加 MemoryOverlay\n"
            "3. 只有当你要重点补风险、挑错、收紧结论时才保留/追加 RiskOverlay\n"
            "4. 为了轻量化，最多选择 2 个专家\n"
            "你必须只输出一个 JSON 对象，不要输出任何额外文本。\n"
            "JSON 格式：\n"
            '{"decision":"answer|ask_user|continue_deliberation","message":"给用户的话；若继续内部讨论可为空",'
            '"question_to_iq":"若继续讨论则填写问题，否则空字符串","selected_experts":["ActionExpert"],'
            '"expert_questions":{"ActionExpert":"..."},"accepted_experts":["ActionExpert"],'
            '"rejected_experts":[],"arbitration_summary":"...","task_continuity":"none|continue|new","task_label":"...","reason":"..."}\n\n'
            f"用户输入：{user_input}\n"
            f"EQ intent：{eq_intent or '(空)'}\n"
            f"EQ emotional_goal：{eq_emotional_goal or '(空)'}\n"
            f"EQ working_hypothesis：{self._compact_text(eq_working_hypothesis, limit=140) or '(空)'}\n"
            f"本轮已启用专家：{', '.join(iq_selected_experts[:3]) or '(空)'}\n"
            f"IQ summary：{self._compact_text(iq_summary, limit=320) or '(空)'}\n"
            f"已讨论轮数：{discussion_count}\n"
        )
        if pending_task:
            prompt += f"待续任务：{self._compact_text(pending_task.get('task', ''), limit=120) or '(空)'}\n"
            prompt += f"待续任务ID：{str(pending_task.get('task_id', '') or '').strip() or '(空)'}\n"
        if current_task:
            prompt += (
                f"当前任务ID：{str(current_task.get('task_id', '') or '').strip() or '(空)'}\n"
                f"当前任务标签：{self._compact_text(current_task.get('task_label', ''), limit=80) or '(空)'}\n"
            )

        messages = self.context.build_messages(
            history=history,
            current_message=prompt,
            mode="eq",
            current_emotion=emotion,
            pad_state=(pad.get("pleasure", 0.0), pad.get("arousal", 0.5), pad.get("dominance", 0.5)),
            internal_iq_summaries=internal_iq_summaries,
        )
        start = time()
        resp = await self.eq_llm.ainvoke(messages)
        end = time()
        print("finalize-messages:",messages)
        print("finalize-resp:",resp)
        print(f"finalize-llm-时间：{end - start}秒")
        raw_text = extract_message_text(resp)
        parsed = self._parse_json(raw_text)
        if parsed is None:
            recovered = self._recover_finalize(raw_text)
            if recovered is not None:
                recovered.setdefault("question_to_iq", "")
                recovered.setdefault("selected_experts", [])
                recovered.setdefault("expert_questions", {})
                recovered.setdefault("accepted_experts", list(iq_selected_experts or [])[:2])
                recovered.setdefault("rejected_experts", [])
                recovered.setdefault("arbitration_summary", "EQ 从不完整输出中恢复了可用结论。")
                recovered.setdefault("reason", "recovered_partial_json")
                return recovered
            return self._fallback_finalize(
                iq_status=iq_status,
                iq_analysis=iq_summary,
                iq_missing_params=iq_missing_params,
                iq_recommended_action=iq_recommended_action,
                iq_selected_experts=iq_selected_experts,
            )

        parsed = self._normalize_finalize_payload(parsed)
        if parsed is None:
            return self._fallback_finalize(
                iq_status=iq_status,
                iq_analysis=iq_summary,
                iq_missing_params=iq_missing_params,
                iq_recommended_action=iq_recommended_action,
                iq_selected_experts=iq_selected_experts,
            )
        decision = str(parsed.get("decision", "") or "").strip().lower()
        selected_experts, expert_questions = self._normalize_expert_plan(
            selected_experts=parsed.get("selected_experts"),
            expert_questions=parsed.get("expert_questions"),
            need_iq=decision == "continue_deliberation",
            question_to_iq=str(parsed.get("question_to_iq", "") or "").strip(),
            pending_task=pending_task,
            user_input=user_input,
        )
        accepted_experts, rejected_experts, arbitration_summary = self._normalize_arbitration(
            accepted_experts=parsed.get("accepted_experts"),
            rejected_experts=parsed.get("rejected_experts"),
            arbitration_summary=str(parsed.get("arbitration_summary", "") or "").strip(),
            iq_selected_experts=iq_selected_experts,
            expert_packets=[],
            decision=decision,
        )
        return {
            "decision": decision,
            "message": str(parsed.get("message", "") or "").strip(),
            "question_to_iq": str(parsed.get("question_to_iq", "") or "").strip(),
            "selected_experts": selected_experts,
            "expert_questions": expert_questions,
            "accepted_experts": accepted_experts,
            "rejected_experts": rejected_experts,
            "arbitration_summary": arbitration_summary,
            "task_continuity": self._normalize_task_continuity(parsed.get("task_continuity")),
            "task_label": self._normalize_task_label(parsed.get("task_label"), user_input=user_input),
            "reason": str(parsed.get("reason", "") or "").strip(),
        }

    @classmethod
    def _normalize_deliberation_payload(cls, parsed: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(parsed, dict):
            return None

        normalized = dict(parsed)
        legacy_decision = cls._map_legacy_eq_decision(parsed.get("decision"))
        legacy_task = str(parsed.get("task", "") or "").strip()

        if legacy_decision == "answer":
            normalized["need_iq"] = False
            normalized.setdefault("final_decision", "answer")
            normalized.setdefault("final_message", str(parsed.get("message", "") or "").strip())
        elif legacy_decision in {"continue_deliberation", "ask_user"}:
            normalized["need_iq"] = True
            normalized.setdefault("question_to_iq", legacy_task or str(parsed.get("message", "") or "").strip())
            normalized.setdefault("final_decision", "")
            normalized.setdefault("final_message", "")

        need_iq = normalized.get("need_iq")
        if not isinstance(need_iq, bool):
            return None
        if not need_iq:
            final_message = str(normalized.get("final_message", "") or "").strip()
            if not final_message:
                return None
            normalized["final_decision"] = "answer"
        normalized["task_continuity"] = cls._normalize_task_continuity(normalized.get("task_continuity"))
        normalized["task_label"] = cls._normalize_task_label(
            normalized.get("task_label"),
            user_input=str(
                normalized.get("question_to_iq", "")
                or normalized.get("final_message", "")
                or parsed.get("task", "")
                or ""
            ),
        )
        return normalized

    @classmethod
    def _normalize_finalize_payload(cls, parsed: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(parsed, dict):
            return None

        normalized = dict(parsed)
        decision = str(normalized.get("decision", "") or "").strip().lower()
        legacy_task = str(normalized.get("task", "") or "").strip()

        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            decision = cls._map_legacy_eq_decision(decision)
        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            return None

        normalized["decision"] = decision
        normalized["message"] = str(normalized.get("message", "") or "").strip()
        normalized["question_to_iq"] = str(normalized.get("question_to_iq", "") or "").strip()
        if decision == "continue_deliberation" and not normalized["question_to_iq"]:
            normalized["question_to_iq"] = legacy_task or normalized["message"]
        if decision != "continue_deliberation":
            normalized["question_to_iq"] = ""
        if decision in {"answer", "ask_user"} and not normalized["message"]:
            return None
        normalized["task_continuity"] = cls._normalize_task_continuity(normalized.get("task_continuity"))
        normalized["task_label"] = cls._normalize_task_label(normalized.get("task_label"), user_input=normalized.get("message", ""))
        return normalized

    @staticmethod
    def _normalize_task_continuity(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"continue", "new", "none"}:
            return text
        if text in {"same", "resume", "old", "existing"}:
            return "continue"
        if text in {"new_task", "newtask", "fresh"}:
            return "new"
        return "none"

    @staticmethod
    def _normalize_task_label(value: Any, *, user_input: str) -> str:
        label = str(value or "").strip()
        if label:
            return label[:120]
        return str(user_input or "").strip()[:120]

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
    def _sanitize_selected_experts(value: Any) -> list[str]:
        allowed = {"ActionExpert", "MemoryOverlay", "RiskOverlay"}
        if not isinstance(value, list):
            return []
        selected: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text in allowed and text not in selected:
                selected.append(text)
        return selected[:3]

    @classmethod
    def _sanitize_expert_questions(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        selected = cls._sanitize_selected_experts(list(value.keys()))
        questions: dict[str, str] = {}
        for key in selected:
            question = str(value.get(key, "") or "").strip()
            if question:
                questions[key] = question
        return questions

    @staticmethod
    def _sanitize_expert_packets(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        packets: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            packets.append(
                {
                    "expert": str(item.get("expert", "") or "").strip(),
                    "status": str(item.get("status", "") or "").strip(),
                    "answer": str(item.get("answer", "") or "").strip(),
                    "confidence": float(item.get("confidence", 0.0) or 0.0),
                    "evidence": [str(x).strip() for x in item.get("evidence", []) if str(x).strip()][:3],
                    "risks": [str(x).strip() for x in item.get("risks", []) if str(x).strip()][:3],
                    "missing": [str(x).strip() for x in item.get("missing", []) if str(x).strip()][:3],
                    "proposed_action": str(item.get("proposed_action", "") or "").strip(),
                }
            )
        return packets[:4]

    @staticmethod
    def _build_expert_summaries(expert_packets: list[dict[str, Any]]) -> list[str]:
        summaries: list[str] = []
        for packet in expert_packets:
            expert = packet.get("expert", "") or "unknown"
            status = packet.get("status", "") or "unknown"
            confidence = float(packet.get("confidence", 0.0) or 0.0)
            answer = str(packet.get("answer", "") or "").strip()
            action = str(packet.get("proposed_action", "") or "").strip()
            missing = [str(item).strip() for item in packet.get("missing", []) if str(item).strip()]
            risks = [str(item).strip() for item in packet.get("risks", []) if str(item).strip()]

            parts = [f"{expert}[{status}|{confidence:.2f}]"]
            if answer:
                parts.append(answer)
            if action:
                parts.append(f"建议动作={action}")
            if missing:
                parts.append(f"缺参={','.join(missing[:2])}")
            if risks:
                parts.append(f"风险={risks[0]}")
            summaries.append("；".join(parts))
        return summaries[:4]

    @staticmethod
    def _build_disagreement_summary(expert_packets: list[dict[str, Any]]) -> str:
        if len(expert_packets) < 2:
            return ""
        actions = {str(packet.get("proposed_action", "") or "").strip() for packet in expert_packets if str(packet.get("proposed_action", "") or "").strip()}
        missing_sets = {tuple(packet.get("missing", []) or []) for packet in expert_packets if packet.get("missing")}
        risk_count = sum(1 for packet in expert_packets if packet.get("risks"))
        parts: list[str] = []
        if len(actions) > 1:
            action_map = ", ".join(
                f"{packet.get('expert', 'unknown')}->{packet.get('proposed_action', '')}"
                for packet in expert_packets
                if str(packet.get("proposed_action", "") or "").strip()
            )
            parts.append(f"专家建议动作不一致：{action_map}")
        if len(missing_sets) > 1:
            parts.append("不同专家对缺参判断不一致")
        if risk_count and risk_count < len(expert_packets):
            parts.append("只有部分专家认为存在明显风险")
        return "；".join(parts[:3])

    @classmethod
    def _normalize_expert_plan(
        cls,
        *,
        selected_experts: Any,
        expert_questions: Any,
        need_iq: bool,
        question_to_iq: str,
        pending_task: dict[str, Any] | None,
        user_input: str,
    ) -> tuple[list[str], dict[str, str]]:
        if not need_iq:
            return [], {}

        selected = cls._sanitize_selected_experts(selected_experts)
        questions = cls._sanitize_expert_questions(expert_questions)
        text = f"{question_to_iq} {user_input}".lower()

        if not selected:
            selected = ["ActionExpert"]
            if pending_task or any(token in text for token in ["继续", "上次", "刚才", "历史", "补充", "之前", "resume"]):
                selected.append("MemoryOverlay")
            elif any(token in text for token in ["风险", "不确定", "谨慎", "删除", "付款", "执行", "命令"]):
                selected.append("RiskOverlay")

        if any(item in selected for item in ["MemoryOverlay", "RiskOverlay"]) and "ActionExpert" not in selected:
            selected.insert(0, "ActionExpert")

        ordered: list[str] = []
        for item in selected:
            if item not in ordered:
                ordered.append(item)

        overlays = [item for item in ordered if item != "ActionExpert"]
        if len(overlays) > 1:
            if pending_task or any(token in text for token in ["继续", "上次", "刚才", "历史", "补充", "之前", "resume"]):
                overlays = [item for item in overlays if item == "MemoryOverlay"][:1] or overlays[:1]
            elif any(token in text for token in ["风险", "不确定", "谨慎", "删除", "付款", "执行", "命令"]):
                overlays = [item for item in overlays if item == "RiskOverlay"][:1] or overlays[:1]
            else:
                overlays = overlays[:1]
        selected = (["ActionExpert"] if "ActionExpert" in ordered else []) + overlays
        if not selected:
            selected = ["ActionExpert"]

        defaults = cls._default_expert_questions(
            question_to_iq=question_to_iq,
            selected_experts=selected,
            pending_task=pending_task,
        )
        return selected[:2], {key: questions.get(key) or defaults.get(key, "") for key in selected[:2]}

    @staticmethod
    def _default_expert_questions(
        *,
        question_to_iq: str,
        selected_experts: list[str],
        pending_task: dict[str, Any] | None,
    ) -> dict[str, str]:
        task = str(pending_task.get("task", "") or "").strip() if pending_task else ""
        defaults: dict[str, str] = {}
        if "ActionExpert" in selected_experts:
            defaults["ActionExpert"] = question_to_iq or "请分析当前请求的可执行性、缺参、证据和下一步建议。"
        if "MemoryOverlay" in selected_experts:
            defaults["MemoryOverlay"] = (
                f"请判断当前输入是否命中历史待续任务“{task}”，并提炼最短历史补丁。"
                if task
                else "请判断当前输入是否需要历史承接，并提炼最短历史补丁。"
            )
        if "RiskOverlay" in selected_experts:
            defaults["RiskOverlay"] = "请只指出当前结论最危险的漏洞、风险或仍需保守的点。"
        return defaults

    @classmethod
    def _normalize_arbitration(
        cls,
        *,
        accepted_experts: Any,
        rejected_experts: Any,
        arbitration_summary: str,
        iq_selected_experts: list[str],
        expert_packets: list[dict[str, Any]],
        decision: str,
    ) -> tuple[list[str], list[str], str]:
        available: list[str] = []
        for item in iq_selected_experts:
            text = str(item or "").strip()
            if text and text not in available:
                available.append(text)
        for packet in expert_packets:
            text = str(packet.get("expert", "") or "").strip()
            if text and text not in available:
                available.append(text)

        accepted = [item for item in cls._sanitize_selected_experts(accepted_experts) if item in available]
        rejected = [item for item in cls._sanitize_selected_experts(rejected_experts) if item in available and item not in accepted]

        if not accepted and available:
            accepted = [available[0]]
        if not rejected and len(available) > len(accepted):
            rejected = [item for item in available if item not in accepted][:2]

        summary = arbitration_summary.strip()
        if not summary:
            accepted_text = "、".join(accepted) if accepted else "无"
            rejected_text = "、".join(rejected) if rejected else "无"
            summary = f"EQ 采纳 {accepted_text}，压过 {rejected_text}，最终决策为 {decision}。"
        return accepted[:3], rejected[:3], summary

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
    def _recover_deliberation(cls, raw: str) -> dict[str, Any] | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        final_message = cls._extract_json_string_field(cleaned, "final_message")
        final_decision = cls._extract_json_string_field(cleaned, "final_decision") or "answer"
        need_iq = cls._extract_json_bool_field(cleaned, "need_iq")
        if final_message and need_iq is False:
            return {
                "intent": cls._extract_json_string_field(cleaned, "intent"),
                "emotional_goal": cls._extract_json_string_field(cleaned, "emotional_goal"),
                "working_hypothesis": cls._extract_json_string_field(cleaned, "working_hypothesis"),
                "need_iq": False,
                "question_to_iq": "",
                "selected_experts": [],
                "expert_questions": {},
                "task_continuity": cls._normalize_task_continuity(cls._extract_json_string_field(cleaned, "task_continuity")),
                "task_label": cls._normalize_task_label(cls._extract_json_string_field(cleaned, "task_label"), user_input=final_message),
                "final_decision": final_decision,
                "final_message": final_message,
                "reason": "recovered_partial_json",
            }
        return None

    @classmethod
    def _recover_finalize(cls, raw: str) -> dict[str, Any] | None:
        cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        message = cls._extract_json_string_field(cleaned, "message")
        decision = cls._extract_json_string_field(cleaned, "decision") or "answer"
        if not message:
            return None
        if decision not in {"answer", "ask_user", "continue_deliberation"}:
            decision = "answer"
        return {
            "decision": decision,
            "message": message,
            "task_continuity": cls._normalize_task_continuity(cls._extract_json_string_field(cleaned, "task_continuity")),
            "task_label": cls._normalize_task_label(cls._extract_json_string_field(cleaned, "task_label"), user_input=message),
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
        pending_task: dict[str, Any] | None,
        emotion: str = "平静",
    ) -> dict[str, Any]:
        if pending_task:
            task = str(pending_task.get("task", "") or "").strip()
            missing = [str(item).strip() for item in pending_task.get("missing_params", []) if str(item).strip()]
            return {
                "intent": "继续先前未完成任务",
                "emotional_goal": "自然地承接上下文",
                "working_hypothesis": "用户很可能在补充上次缺失的信息",
                "need_iq": True,
                "question_to_iq": (
                    f"请结合用户刚刚的输入，继续任务：{task}。"
                    + (f"重点确认是否已补足这些参数：{json.dumps(missing, ensure_ascii=False)}。" if missing else "")
                ),
                "selected_experts": ["ActionExpert", "MemoryOverlay"],
                "expert_questions": {
                    "ActionExpert": f"请继续任务：{task}，并判断是否还缺关键参数。",
                    "MemoryOverlay": "请判断当前输入是否命中待续任务，并提炼最短历史补丁。",
                },
                "task_continuity": "continue",
                "task_label": task or user_input,
                "final_decision": "",
                "final_message": "",
                "reason": "resume_pending_task",
            }
        if cls._looks_task_like(user_input):
            return {
                "intent": "用户需要事实分析或任务帮助",
                "emotional_goal": "既靠谱又不失温度",
                "working_hypothesis": "应先征询 IQ 获取事实与可执行性判断",
                "need_iq": True,
                "question_to_iq": "请分析用户请求的可执行性、需要的事实或工具、风险与下一步建议。",
                "selected_experts": ["ActionExpert"],
                "expert_questions": {
                    "ActionExpert": "请分析用户请求的可执行性、缺参、工具需求与下一步建议。",
                },
                "task_continuity": "new",
                "task_label": user_input,
                "final_decision": "",
                "final_message": "",
                "reason": "task_like_request",
            }
        return {
            "intent": "用户更需要陪伴或轻量回应",
            "emotional_goal": "先建立连接感",
            "working_hypothesis": "此时无需征询 IQ",
            "need_iq": False,
            "question_to_iq": "",
            "selected_experts": [],
            "expert_questions": {},
            "task_continuity": "none",
            "task_label": "",
            "final_decision": "answer",
            "final_message": build_companion_prompt(emotion),
            "reason": "lightweight_conversation",
        }

    @classmethod
    def _fallback_finalize(
        cls,
        *,
        iq_status: str,
        iq_analysis: str,
        iq_missing_params: list[str],
        iq_recommended_action: str,
        iq_selected_experts: list[str],
    ) -> dict[str, Any]:
        if iq_missing_params or iq_status == "needs_input" or iq_recommended_action == "ask_user":
            return {
                "decision": "ask_user",
                "message": build_missing_info_prompt(iq_missing_params),
                "question_to_iq": "",
                "selected_experts": [],
                "expert_questions": {},
                "accepted_experts": list(iq_selected_experts or [])[:2],
                "rejected_experts": [],
                "arbitration_summary": "EQ 判断当前应先向用户追问缺失信息。",
                "task_continuity": "continue",
                "task_label": iq_analysis,
                "reason": "needs_input",
            }
        if iq_status == "uncertain" or iq_recommended_action == "continue_deliberation":
            return {
                "decision": "continue_deliberation",
                "message": "",
                "question_to_iq": "请补充最关键的证据、风险和下一步建议，帮助我做最终判断。",
                "selected_experts": ["ActionExpert", "RiskOverlay"],
                "expert_questions": {
                    "ActionExpert": "请补充最关键证据和可执行建议，不要重复全部分析。",
                    "RiskOverlay": "请只指出当前结论最危险的漏洞或仍需保守的点。",
                },
                "accepted_experts": ["RiskOverlay"] if iq_status == "uncertain" else ["ActionExpert"],
                "rejected_experts": [],
                "arbitration_summary": "EQ 判断现有结论还不够稳，要求继续内部讨论。",
                "task_continuity": "continue",
                "task_label": iq_analysis,
                "reason": "need_more_internal_analysis",
            }
        return {
            "decision": "answer",
            "message": iq_analysis or "嗯，我把思路理顺了。你要是愿意，我们就顺着这个继续。",
            "question_to_iq": "",
            "selected_experts": [],
            "expert_questions": {},
            "accepted_experts": ["ActionExpert"],
            "rejected_experts": [],
            "arbitration_summary": "EQ 采纳当前主专家结论并直接对外回答。",
            "task_continuity": "continue",
            "task_label": iq_analysis,
            "reason": iq_status or "answer",
        }

__all__ = ["EQService"]
