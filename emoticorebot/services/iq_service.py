"""IQ Service - EQ 主导下的轻量稀疏 MoE 协调层。"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from loguru import logger

from emoticorebot.core.context import ContextBuilder
from emoticorebot.experts import ActionExpert, ExpertContext, ExpertPacket, ExpertRegistry, MemoryOverlay, RiskOverlay
from emoticorebot.tools import ToolRegistry
from emoticorebot.utils.llm_utils import extract_message_text


class IQService:
    """IQ 参谋层。

    形态：
    - 默认仅启用 `ActionExpert`
    - 命中历史承接时追加 `MemoryOverlay`
    - 低置信度或高风险时追加 `RiskOverlay`
    """

    _VALID_STATUS = {"completed", "needs_input", "uncertain", "failed"}
    _VALID_ACTIONS = {"answer", "ask_user", "continue_deliberation"}

    def __init__(
        self,
        iq_llm,
        tool_registry: ToolRegistry | None,
        context_builder: ContextBuilder,
        max_iterations: int = 30,
    ):
        self.iq_llm = iq_llm
        self.tools = tool_registry
        self.context = context_builder
        self.max_iterations = max_iterations

        self.experts = ExpertRegistry()
        self.experts.register(ActionExpert(self._run_action_expert))
        self.experts.register(MemoryOverlay(self.context.memory_facade))
        self.experts.register(RiskOverlay())

    async def run_task(
        self,
        task: str,
        history: list[dict[str, Any]],
        emotion: str,
        pad: dict[str, float],
        channel: str,
        chat_id: str,
        intent_params: dict[str, Any] | None = None,
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """执行轻量稀疏 MoE，并返回融合后的参谋包。"""
        expert_questions = self._extract_expert_questions(intent_params)
        selected_experts = self._select_experts(
            task=task,
            history=history,
            intent_params=intent_params,
        )
        packets: list[ExpertPacket] = []

        pending_task = self._build_pending_task(intent_params)
        base_context = ExpertContext(
            task=task,
            user_input=self._latest_user_input(history),
            history=history,
            intent_params=intent_params,
            pending_task=pending_task,
            channel=channel,
            chat_id=chat_id,
            media=media,
            on_progress=on_progress,
        )

        memory_packet: ExpertPacket | None = None
        if "MemoryOverlay" in selected_experts:
            expert = self.experts.get("MemoryOverlay")
            if expert is not None:
                memory_packet = await expert.run(
                    ExpertContext(
                        task=expert_questions.get("MemoryOverlay", task),
                        user_input=base_context.user_input,
                        history=history,
                        intent_params=intent_params,
                        pending_task=pending_task,
                        channel=channel,
                        chat_id=chat_id,
                        media=media,
                        on_progress=on_progress,
                    )
                )
                packets.append(memory_packet)

        action_context = ExpertContext(
            task=expert_questions.get("ActionExpert", task),
            user_input=base_context.user_input,
            history=history,
            intent_params=self._merge_intent_params(intent_params, memory_packet),
            pending_task=pending_task,
            channel=channel,
            chat_id=chat_id,
            media=media,
            on_progress=on_progress,
            memory_packet=memory_packet.to_dict() if memory_packet is not None else None,
        )
        action_expert = self.experts.get("ActionExpert")
        if action_expert is None:
            raise RuntimeError("ActionExpert is not registered")
        action_packet = await action_expert.run(action_context)
        packets.append(action_packet)

        allow_auto_risk = not self._has_explicit_selected_experts(intent_params)
        if "RiskOverlay" in selected_experts or (
            allow_auto_risk and self._should_add_risk_overlay(task=task, action_packet=action_packet)
        ):
            if "RiskOverlay" not in selected_experts:
                selected_experts.append("RiskOverlay")
            risk_expert = self.experts.get("RiskOverlay")
            if risk_expert is not None:
                risk_packet = await risk_expert.run(
                    ExpertContext(
                        task=expert_questions.get("RiskOverlay", task),
                        user_input=base_context.user_input,
                        history=history,
                        intent_params=intent_params,
                        pending_task=pending_task,
                        channel=channel,
                        chat_id=chat_id,
                        media=media,
                        on_progress=on_progress,
                        memory_packet=memory_packet.to_dict() if memory_packet is not None else None,
                        action_packet=action_packet.metadata.get("raw_packet", {}),
                    )
                )
                packets.append(risk_packet)

        return self._merge_packets(selected_experts=selected_experts, packets=packets)

    async def _run_action_expert(self, context: ExpertContext) -> dict[str, Any]:
        current_message = self._build_task_message(
            task=context.task,
            intent_params=context.intent_params,
            memory_packet=context.memory_packet,
        )
        messages = self.context.build_messages(
            history=context.history,
            current_message=current_message,
            mode="iq",
            media=context.media,
        )

        lc_messages = self._to_langchain_messages(messages)
        tool_calls: list[dict[str, Any]] = []

        llm = self.iq_llm
        if self.tools is not None:
            definitions = self.tools.get_definitions()
            if definitions:
                llm = self.iq_llm.bind_tools(definitions)

        max_calls = max(1, self.max_iterations)
        for iteration in range(max_calls):
            resp = await llm.ainvoke(lc_messages)

            if not getattr(resp, "tool_calls", None):
                content = self._msg_text(resp)
                packet = self._parse_advisor_packet(content)
                if packet is None:
                    packet = self._fallback_packet(content, tool_calls=tool_calls)
                return self._finalize_packet(packet, tool_calls=tool_calls, iterations=iteration + 1)

            lc_messages.append(
                AIMessage(
                    content=self._msg_text(resp),
                    tool_calls=[
                        {"id": tc["id"], "name": tc["name"], "args": tc.get("args", {})}
                        for tc in resp.tool_calls
                    ],
                )
            )

            for tc in resp.tool_calls:
                name = tc["name"]
                args = tc.get("args", {})
                logger.debug("IQ tool call: {} with args {}", name, args)

                result = await self.tools.execute(name, args) if self.tools is not None else "Error: no tools available"
                tool_calls.append(
                    {
                        "tool": name,
                        "args": args,
                        "result": self._summarize_response(result, limit=500),
                    }
                )
                lc_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))

                if context.on_progress:
                    await context.on_progress(f"执行工具: {name}")

        return self._finalize_packet(
            {
                "status": "uncertain",
                "analysis": "达到最大工具迭代次数，未得到稳定结论。",
                "evidence": [],
                "risks": ["工具调用轮数已耗尽，当前结论可能不完整。"],
                "missing": [],
                "options": [],
                "recommended_action": "continue_deliberation",
                "confidence": 0.15,
            },
            tool_calls=tool_calls,
            iterations=max_calls,
        )

    def _select_experts(
        self,
        *,
        task: str,
        history: list[dict[str, Any]],
        intent_params: dict[str, Any] | None,
    ) -> list[str]:
        explicit = self._extract_selected_experts(intent_params)
        if explicit:
            return explicit
        selected = ["ActionExpert"]
        if self._should_use_memory_overlay(task=task, history=history, intent_params=intent_params):
            selected.append("MemoryOverlay")
        return selected

    @staticmethod
    def _extract_selected_experts(intent_params: dict[str, Any] | None) -> list[str]:
        allowed = {"ActionExpert", "MemoryOverlay", "RiskOverlay"}
        params = intent_params or {}
        value = params.get("selected_experts")
        if not isinstance(value, list):
            return []
        selected: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text in allowed and text not in selected:
                selected.append(text)
        return selected[:3]

    @staticmethod
    def _has_explicit_selected_experts(intent_params: dict[str, Any] | None) -> bool:
        return bool(IQService._extract_selected_experts(intent_params))

    @staticmethod
    def _extract_expert_questions(intent_params: dict[str, Any] | None) -> dict[str, str]:
        params = intent_params or {}
        value = params.get("expert_questions")
        if not isinstance(value, dict):
            return {}
        questions: dict[str, str] = {}
        for key, question in value.items():
            key_text = str(key or "").strip()
            question_text = str(question or "").strip()
            if key_text and question_text:
                questions[key_text] = question_text
        return questions

    def _should_use_memory_overlay(
        self,
        *,
        task: str,
        history: list[dict[str, Any]],
        intent_params: dict[str, Any] | None,
    ) -> bool:
        params = intent_params or {}
        if params.get("resume_task") or params.get("missing_params"):
            return True
        latest = self._latest_user_input(history).strip().lower()
        task_text = f"{task} {latest}".lower()
        short_reply = len(latest) <= 12 if latest else False
        keywords = ["继续", "刚才", "上次", "还是", "按之前", "那个", "补充", "之前", "resume"]
        if any(keyword in task_text for keyword in keywords):
            return True
        if short_reply and history:
            last_assistant = next((item for item in reversed(history) if item.get("role") == "assistant"), None)
            if last_assistant is not None:
                content = str(last_assistant.get("content", "") or "")
                if any(token in content for token in ["请提供", "还差", "缺少", "哪个", "什么时间", "什么城市"]):
                    return True
        return False

    @staticmethod
    def _should_add_risk_overlay(*, task: str, action_packet: ExpertPacket) -> bool:
        task_text = (task or "").lower()
        if action_packet.confidence < 0.7:
            return True
        if action_packet.missing:
            return True
        if action_packet.metadata.get("tool_calls"):
            return True
        return any(token in task_text for token in ["删除", "付款", "转账", "发送", "执行", "shell", "命令"])

    @classmethod
    def _merge_packets(cls, *, selected_experts: list[str], packets: list[ExpertPacket]) -> dict[str, Any]:
        action_packet = next((item for item in packets if item.expert == "ActionExpert"), None)
        memory_packet = next((item for item in packets if item.expert == "MemoryOverlay"), None)
        risk_packet = next((item for item in packets if item.expert == "RiskOverlay"), None)

        if action_packet is None:
            return cls._finalize_packet(
                {
                    "status": "failed",
                    "analysis": "没有拿到 ActionExpert 的结果。",
                    "evidence": [],
                    "risks": ["主专家缺席，无法继续。"],
                    "missing": [],
                    "options": [],
                    "recommended_action": "continue_deliberation",
                    "confidence": 0.1,
                },
                tool_calls=[],
                iterations=0,
            ) | {
                "selected_experts": selected_experts,
                "expert_packets": [packet.to_dict() for packet in packets],
            }

        raw_action = action_packet.metadata.get("raw_packet", {})
        evidence = list(action_packet.evidence)
        risks = list(action_packet.risks)
        missing = list(action_packet.missing)
        confidence = float(action_packet.confidence or 0.0)
        recommended_action = action_packet.proposed_action or "answer"

        if memory_packet is not None and memory_packet.confidence >= 0.55:
            summary = str(memory_packet.metadata.get("summary", "") or memory_packet.answer).strip()
            if summary:
                evidence.insert(0, f"历史补丁：{summary}")
            resume_task = str(memory_packet.metadata.get("resume_task", "") or "").strip()
            if resume_task and not raw_action.get("analysis"):
                raw_action["analysis"] = f"当前请求与历史任务“{resume_task}”相关。"

        if risk_packet is not None:
            risks.extend(risk_packet.risks)
            for item in risk_packet.evidence:
                evidence.append(f"风险补丁：{item}")
            if risk_packet.proposed_action == "ask_user":
                recommended_action = "ask_user"
            elif risk_packet.proposed_action == "continue_deliberation" and recommended_action == "answer":
                recommended_action = "continue_deliberation"
            if confidence > 0:
                confidence = max(0.0, min(0.95, confidence * 0.85 + float(risk_packet.confidence or 0.0) * 0.15))

        missing = list(dict.fromkeys(item for item in missing if item))
        evidence = list(dict.fromkeys(item for item in evidence if item))[:6]
        risks = list(dict.fromkeys(item for item in risks if item))[:6]

        merged_packet = cls._finalize_packet(
            {
                "status": action_packet.status,
                "analysis": action_packet.answer,
                "evidence": evidence,
                "risks": risks,
                "missing": missing,
                "options": list(raw_action.get("options", []) or []),
                "recommended_action": recommended_action,
                "confidence": confidence,
            },
            tool_calls=list(raw_action.get("tool_calls", []) or []),
            iterations=int(raw_action.get("iterations", 0) or 0),
        )
        merged_packet["selected_experts"] = selected_experts
        merged_packet["expert_packets"] = [packet.to_dict() for packet in packets]
        return merged_packet

    @staticmethod
    def _build_pending_task(intent_params: dict[str, Any] | None) -> dict[str, Any] | None:
        params = intent_params or {}
        resume_task = str(params.get("resume_task", "") or "").strip()
        if not resume_task:
            return None
        return {
            "task": resume_task,
            "missing_params": list(params.get("missing_params", []) or []),
        }

    @staticmethod
    def _merge_intent_params(
        intent_params: dict[str, Any] | None,
        memory_packet: ExpertPacket | None,
    ) -> dict[str, Any] | None:
        if intent_params is None and memory_packet is None:
            return None
        merged = dict(intent_params or {})
        if memory_packet is None:
            return merged
        resume_task = str(memory_packet.metadata.get("resume_task", "") or "").strip()
        if resume_task and not merged.get("resume_task"):
            merged["resume_task"] = resume_task
        memory_missing = [str(item).strip() for item in memory_packet.missing if str(item).strip()]
        if memory_missing and not merged.get("missing_params"):
            merged["missing_params"] = memory_missing
        return merged

    @staticmethod
    def _latest_user_input(history: list[dict[str, Any]]) -> str:
        for item in reversed(history):
            if item.get("role") == "user":
                return str(item.get("content", "") or "")
        return ""

    def _build_task_message(
        self,
        *,
        task: str,
        intent_params: dict[str, Any] | None,
        memory_packet: dict[str, Any] | None,
    ) -> str:
        current_message = (
            "你在回答 EQ 的内部问题。\n"
            "如果需要工具，请先调用工具；最终只输出一个 JSON 对象。\n"
            "JSON 格式：\n"
            '{"status":"completed|needs_input|uncertain|failed","analysis":"...","evidence":["..."],'
            '"risks":["..."],"missing":["..."],"options":[{"name":"...","description":"...","tradeoff":"..."}],'
            '"recommended_action":"answer|ask_user|continue_deliberation","confidence":0.0}\n\n'
            f"EQ 的问题/任务：{task}"
        )

        if intent_params:
            current_message += (
                "\n\n[Router Extracted Params]\n"
                f"{json.dumps(intent_params, ensure_ascii=False)}\n"
                "请优先使用以上参数理解当前内部任务。\n"
            )
            followup_answer = str(intent_params.get("followup_answer", "")).strip()
            missing_params = intent_params.get("missing_params") or []
            resume_task = str(intent_params.get("resume_task", "")).strip()
            eq_accepted = [str(item).strip() for item in (intent_params.get("eq_accepted_experts") or []) if str(item).strip()]
            eq_rejected = [str(item).strip() for item in (intent_params.get("eq_rejected_experts") or []) if str(item).strip()]
            eq_arbitration = str(intent_params.get("eq_arbitration_summary", "") or "").strip()
            if resume_task:
                current_message += f"\n[Resume Task]\n原待续任务：{resume_task}\n"
            if followup_answer:
                current_message += f"\n[Follow-up Answer]\n用户刚补充的信息：{followup_answer}\n"
            if missing_params:
                current_message += f"需要重点确认是否已补足这些缺参：{json.dumps(missing_params, ensure_ascii=False)}\n"
            if eq_arbitration or eq_accepted or eq_rejected:
                current_message += "\n[Previous EQ Arbitration]\n"
                if eq_arbitration:
                    current_message += f"上一轮 EQ 裁决：{eq_arbitration}\n"
                if eq_accepted:
                    current_message += f"上一轮采纳专家：{json.dumps(eq_accepted, ensure_ascii=False)}\n"
                if eq_rejected:
                    current_message += f"上一轮压过专家：{json.dumps(eq_rejected, ensure_ascii=False)}\n"
                current_message += "请基于这份裁决继续补强，不要重复已经被 EQ 明确采纳的部分。\n"

        if memory_packet:
            summary = str(memory_packet.get("metadata", {}).get("summary", "") or memory_packet.get("answer", "")).strip()
            resume_task = str(memory_packet.get("metadata", {}).get("resume_task", "") or "").strip()
            missing = [str(item).strip() for item in memory_packet.get("missing", []) if str(item).strip()]
            evidence = [str(item).strip() for item in memory_packet.get("evidence", []) if str(item).strip()]
            current_message += "\n[Memory Overlay]\n"
            if summary:
                current_message += f"summary: {summary}\n"
            if resume_task:
                current_message += f"resume_task: {resume_task}\n"
            if missing:
                current_message += f"missing_params: {json.dumps(missing, ensure_ascii=False)}\n"
            if evidence:
                current_message += "facts:\n" + "\n".join(f"- {item}" for item in evidence[:3]) + "\n"

        return current_message

    @staticmethod
    def _msg_text(msg: Any) -> str:
        return extract_message_text(msg)

    @staticmethod
    def _to_langchain_messages(messages: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                out.append(SystemMessage(content=content))
            elif role == "assistant":
                out.append(AIMessage(content=content))
            elif role == "tool":
                out.append(ToolMessage(content=content, tool_call_id=m.get("tool_call_id", "tool")))
            else:
                out.append(HumanMessage(content=content))
        return out

    @classmethod
    def _parse_advisor_packet(cls, content: str) -> dict[str, Any] | None:
        raw = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL).strip()
        if not raw:
            return None
        parsed = cls._extract_json(raw)
        if parsed is None:
            return None
        return {
            "status": cls._normalize_status(parsed.get("status")),
            "analysis": str(parsed.get("analysis") or parsed.get("content") or "").strip(),
            "evidence": cls._sanitize_str_list(parsed.get("evidence")),
            "risks": cls._sanitize_str_list(parsed.get("risks")),
            "missing": cls._sanitize_str_list(parsed.get("missing") or parsed.get("missing_params")),
            "options": cls._sanitize_options(parsed.get("options")),
            "recommended_action": cls._normalize_action(parsed.get("recommended_action")),
            "confidence": cls._safe_confidence(parsed.get("confidence")),
        }

    @classmethod
    def _finalize_packet(
        cls,
        packet: dict[str, Any],
        *,
        tool_calls: list[dict[str, Any]],
        iterations: int,
    ) -> dict[str, Any]:
        status = cls._normalize_status(packet.get("status"))
        analysis = str(packet.get("analysis") or "").strip()
        evidence = cls._sanitize_str_list(packet.get("evidence"))
        risks = cls._sanitize_str_list(packet.get("risks"))
        missing = cls._sanitize_str_list(packet.get("missing") or packet.get("missing_params"))
        options = cls._sanitize_options(packet.get("options"))
        recommended_action = cls._normalize_action(packet.get("recommended_action"))
        confidence = cls._safe_confidence(packet.get("confidence"))

        if not status:
            if missing:
                status = "needs_input"
            elif analysis:
                status = "completed"
            else:
                status = "uncertain"
        if not recommended_action:
            recommended_action = cls._infer_action(status)
        if confidence <= 0.0:
            confidence = cls._estimate_confidence(
                analysis=analysis,
                tool_calls=tool_calls,
                status=status,
                evidence=evidence,
            )
        if not analysis:
            analysis = cls._default_analysis(status, missing)

        rationale_summary = cls._summarize_response(analysis, limit=240)
        return {
            "status": status,
            "analysis": analysis,
            "evidence": evidence,
            "risks": risks,
            "missing": missing,
            "options": options,
            "recommended_action": recommended_action,
            "confidence": confidence,
            "rationale_summary": rationale_summary,
            "tool_calls": tool_calls,
            "iterations": iterations,
        }

    @classmethod
    def _fallback_packet(cls, content: str, *, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        text = (content or "").strip()
        missing = cls._extract_missing_params(text)
        lower = text.lower()

        if missing:
            return {
                "status": "needs_input",
                "analysis": text or "信息还不够，无法继续分析。",
                "evidence": [],
                "risks": ["关键信息缺失，继续判断可能失真。"],
                "missing": missing,
                "options": [],
                "recommended_action": "ask_user",
                "confidence": 0.2,
            }
        if any(token in lower for token in ["error", "失败", "无法", "未找到", "异常"]):
            return {
                "status": "failed",
                "analysis": text or "执行失败，缺少可靠结论。",
                "evidence": cls._tool_evidence(tool_calls),
                "risks": ["已有执行异常，直接回答可能误导用户。"],
                "missing": [],
                "options": [],
                "recommended_action": "continue_deliberation",
                "confidence": 0.2,
            }
        if any(token in lower for token in ["不确定", "可能", "也许", "uncertain", "not sure"]):
            return {
                "status": "uncertain",
                "analysis": text or "当前信息不足以形成稳定结论。",
                "evidence": cls._tool_evidence(tool_calls),
                "risks": ["结论不稳定，需要进一步核实。"],
                "missing": [],
                "options": [],
                "recommended_action": "continue_deliberation",
                "confidence": 0.35,
            }
        return {
            "status": "completed",
            "analysis": text or "已完成分析。",
            "evidence": cls._tool_evidence(tool_calls),
            "risks": [],
            "missing": [],
            "options": [],
            "recommended_action": "answer",
            "confidence": cls._estimate_confidence(
                analysis=text,
                tool_calls=tool_calls,
                status="completed",
                evidence=cls._tool_evidence(tool_calls),
            ),
        }

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        mapping = {
            "success": "completed",
            "completed": "completed",
            "done": "completed",
            "needs_input": "needs_input",
            "ask_user": "needs_input",
            "uncertain": "uncertain",
            "retry": "uncertain",
            "failed": "failed",
            "error": "failed",
        }
        normalized = mapping.get(raw, raw)
        return normalized if normalized in cls._VALID_STATUS else ""

    @classmethod
    def _normalize_action(cls, value: Any) -> str:
        raw = str(value or "").strip().lower()
        return raw if raw in cls._VALID_ACTIONS else ""

    @staticmethod
    def _sanitize_str_list(value: Any, limit: int = 6) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                items.append(text)
            if len(items) >= limit:
                break
        return items

    @classmethod
    def _sanitize_options(cls, value: Any, limit: int = 4) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        options: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            option = {
                "name": str(item.get("name") or item.get("title") or "").strip(),
                "description": str(item.get("description") or item.get("content") or "").strip(),
                "tradeoff": str(item.get("tradeoff") or item.get("risk") or "").strip(),
            }
            if option["name"] or option["description"]:
                options.append(option)
            if len(options) >= limit:
                break
        return options

    @staticmethod
    def _safe_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except Exception:
            return 0.0
        return max(0.0, min(0.95, confidence))

    @staticmethod
    def _extract_missing_params(content: str) -> list[str]:
        missing: list[str] = []
        patterns = [
            r"哪个([\u4e00-\u9fa5A-Za-z0-9_-]+)",
            r"需要(?:补充|提供)([\u4e00-\u9fa5A-Za-z0-9_、，,\- ]+?)(?:信息|参数|内容|。|$)",
            r"请提供([\u4e00-\u9fa5A-Za-z0-9_、，,\- ]+?)(?:信息|参数|内容|。|$)",
            r"缺少([\u4e00-\u9fa5A-Za-z0-9_、，,\- ]+?)(?:信息|参数|内容|。|$)",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, content or ""):
                text = str(match).strip(" ：:，,。.？?\n\t")
                if text:
                    missing.append(text)
        cleaned = list(dict.fromkeys(item for item in missing if item))
        return cleaned[:5]

    @staticmethod
    def _tool_evidence(tool_calls: list[dict[str, Any]], limit: int = 3) -> list[str]:
        evidence: list[str] = []
        for call in tool_calls[:limit]:
            tool = str(call.get("tool", "")).strip()
            result = str(call.get("result", "")).strip()
            if tool and result:
                evidence.append(f"{tool}: {result}")
        return evidence

    @classmethod
    def _infer_action(cls, status: str) -> str:
        if status == "needs_input":
            return "ask_user"
        if status in {"uncertain", "failed"}:
            return "continue_deliberation"
        return "answer"

    @staticmethod
    def _default_analysis(status: str, missing: list[str]) -> str:
        if status == "needs_input" and missing:
            return f"还缺少这些关键信息：{'、'.join(missing)}。"
        if status == "failed":
            return "执行出现异常，暂时无法给出可靠结论。"
        if status == "uncertain":
            return "当前结论还不稳定，需要进一步分析。"
        return "已完成分析。"

    @staticmethod
    def _summarize_response(content: str, limit: int = 240) -> str:
        summary = " ".join((content or "").split())
        if len(summary) <= limit:
            return summary
        return summary[: limit - 1] + "…"

    @classmethod
    def _estimate_confidence(
        cls,
        *,
        analysis: str,
        tool_calls: list[dict[str, Any]],
        status: str,
        evidence: list[str],
    ) -> float:
        if status == "needs_input":
            return 0.2
        if status == "failed":
            return 0.15
        confidence = 0.45 if status == "uncertain" else 0.6
        confidence += min(0.2, 0.06 * len(tool_calls))
        confidence += min(0.12, 0.04 * len(evidence))
        if analysis and "不确定" not in analysis and "无法" not in analysis:
            confidence += 0.08
        return max(0.0, min(0.95, confidence))


__all__ = ["IQService"]
