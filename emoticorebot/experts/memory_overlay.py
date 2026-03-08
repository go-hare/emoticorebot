"""Rule-first memory overlay for lightweight MoE."""

from __future__ import annotations

from typing import Any

from emoticorebot.experts.base import ExpertContext, ExpertPacket
from emoticorebot.memory.memory_facade import MemoryFacade


class MemoryOverlay:
    name = "MemoryOverlay"

    def __init__(self, memory: MemoryFacade):
        self.memory = memory

    async def run(self, context: ExpertContext) -> ExpertPacket:
        pending = context.pending_task or {}
        intent_params = context.intent_params or {}
        user_input = str(context.user_input or "").strip()
        resume_task = str(intent_params.get("resume_task", "") or pending.get("task", "") or "").strip()
        missing = [str(item).strip() for item in (intent_params.get("missing_params") or pending.get("missing_params") or []) if str(item).strip()]

        if resume_task:
            evidence = [f"待续任务：{resume_task}"]
            if missing:
                evidence.append(f"历史缺参：{'、'.join(missing[:3])}")
            if user_input:
                evidence.append(f"当前补充：{user_input}")
            return ExpertPacket(
                expert=self.name,
                status="completed",
                answer="命中待续任务，当前输入大概率是在补充历史任务上下文。",
                confidence=0.92,
                evidence=evidence,
                missing=[] if user_input else missing,
                proposed_action="answer",
                metadata={
                    "kind": "pending_resume",
                    "summary": "命中待续任务，可把当前输入视为历史任务补充。",
                    "resume_task": resume_task,
                    "relevant_memories": [],
                },
            )

        query = user_input or context.task
        active_plans = self.memory.plans.list_active(k=5)
        matched_plan = self._match_plan(active_plans, query)
        if matched_plan is not None:
            title = str(matched_plan.get("title", "") or "").strip()
            blockers = [str(item).strip() for item in matched_plan.get("blockers", []) if str(item).strip()]
            evidence = [f"历史计划：{title}"]
            if blockers:
                evidence.append(f"阻塞项：{'、'.join(blockers[:3])}")
            next_action = str(matched_plan.get("next_action", "") or "").strip()
            if next_action:
                evidence.append(f"下步动作：{next_action}")
            return ExpertPacket(
                expert=self.name,
                status="completed",
                answer="命中活跃历史计划，当前问题与过去未完成事项高度相关。",
                confidence=0.78,
                evidence=evidence,
                missing=blockers,
                proposed_action="answer" if not blockers else "ask_user",
                metadata={
                    "kind": "plan_resume",
                    "summary": f"命中历史计划：{title}",
                    "resume_task": title,
                    "relevant_memories": [matched_plan],
                },
            )

        return ExpertPacket(
            expert=self.name,
            status="completed",
            answer="没有命中足够强的历史任务或记忆线索。",
            confidence=0.25,
            risks=[],
            proposed_action="answer",
            metadata={
                "kind": "none",
                "summary": "未命中明显历史补丁。",
                "resume_task": "",
                "relevant_memories": [],
            },
        )

    @staticmethod
    def _match_plan(plans: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
        query_text = (query or "").strip().lower()
        if not query_text:
            return plans[0] if plans else None
        query_tokens = set(query_text.split())
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for plan in plans:
            title = str(plan.get("title", "") or "").strip().lower()
            next_action = str(plan.get("next_action", "") or "").strip().lower()
            text = f"{title} {next_action}".strip()
            score = 0.0
            if query_text and query_text in text:
                score += 1.0
            title_tokens = set(text.split())
            score += 0.15 * len(query_tokens & title_tokens)
            if score > best[0]:
                best = (score, plan)
        return best[1] if best[0] >= 0.3 else None


__all__ = ["MemoryOverlay"]
