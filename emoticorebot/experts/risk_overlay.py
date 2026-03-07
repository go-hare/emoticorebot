"""Cheap heuristic risk overlay for lightweight MoE."""

from __future__ import annotations

from emoticorebot.experts.base import ExpertContext, ExpertPacket


class RiskOverlay:
    name = "RiskOverlay"

    async def run(self, context: ExpertContext) -> ExpertPacket:
        action_packet = context.action_packet or {}
        analysis = str(action_packet.get("analysis", "") or "").strip()
        tool_calls = list(action_packet.get("tool_calls", []) or [])
        missing = [str(item).strip() for item in action_packet.get("missing", []) if str(item).strip()]
        confidence = float(action_packet.get("confidence", 0.0) or 0.0)
        task_text = f"{context.task} {context.user_input}".lower()

        risks: list[str] = []
        evidence: list[str] = []
        proposed_action = "answer"
        status = "completed"

        if missing:
            risks.append("仍有关键缺参，直接推进可能导致回答偏题。")
            evidence.append(f"缺失参数：{'、'.join(missing[:3])}")
            proposed_action = "ask_user"
        if tool_calls:
            risks.append("当前结论依赖工具结果，需要注意工具返回是否完整或过期。")
            evidence.append(f"工具调用数：{len(tool_calls)}")
        if confidence < 0.65:
            risks.append("主专家置信度偏低，建议保守表达。")
            evidence.append(f"主专家置信度：{confidence:.2f}")
            proposed_action = "continue_deliberation" if not missing else "ask_user"
        if any(token in task_text for token in ["删除", "付款", "转账", "发送", "执行", "shell", "命令"]):
            risks.append("该任务可能触发外部动作，最终对外答复需要更明确边界。")
            evidence.append("命中高风险动作关键词")
            if not missing and confidence < 0.8:
                proposed_action = "continue_deliberation"

        if not risks:
            return ExpertPacket(
                expert=self.name,
                status="completed",
                answer="未发现需要额外保守处理的风险。",
                confidence=0.55,
                evidence=evidence,
                risks=[],
                missing=missing,
                proposed_action="answer",
                metadata={
                    "summary": "未发现额外高风险点。",
                },
            )

        return ExpertPacket(
            expert=self.name,
            status=status,
            answer=analysis or "已完成风险扫描。",
            confidence=0.68,
            evidence=evidence,
            risks=risks,
            missing=missing,
            proposed_action=proposed_action,
            metadata={
                "summary": risks[0] if risks else "无额外风险",
            },
        )


__all__ = ["RiskOverlay"]
