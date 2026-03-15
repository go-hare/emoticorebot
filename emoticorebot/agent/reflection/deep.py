"""Proposal-only deep reflection service."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from emoticorebot.types import DeepReflectionOutput


@dataclass(frozen=True)
class DeepReflectionResult:
    summary: str = ""
    memory_ids: list[str] = field(default_factory=list)
    memory_count: int = 0
    skill_hint_count: int = 0
    materialized_skills: list[str] = field(default_factory=list)
    materialized_skill_count: int = 0
    updated_soul: bool = False
    updated_user: bool = False
    user_updates: list[str] = field(default_factory=list)
    soul_updates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DeepReflectionProposal:
    summary: str = ""
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)
    user_updates: list[str] = field(default_factory=list)
    soul_updates: list[str] = field(default_factory=list)


class DeepReflectionService:
    """Consolidate recent cognitive events into a structured deep-reflection proposal."""

    _PROMPT = """
你是 `brain` 的深反思过程。

请严格按结构化字段填写，不要补充额外说明。

任务：
1. 阅读最近的 `cognitive_event`。
2. 只提炼真正稳定的长期价值。
3. 为统一长期记忆存储产出 `memory_candidates`。
4. 只有在重复执行模式明显可复用时，才产出 `skill_hint` 候选。

规则：
- 不要复制原始日志或大段文本。
- 优先给出稳定结论，而不是一次性噪声。
- 如果证据不足，直接返回空列表。
        - 没有内容时，字符串字段返回 `""`，数组字段返回 `[]`，对象字段返回 `{{}}`。
- `user_updates` / `soul_updates` 的每一项都必须是一条可直接写入 Markdown 列表的稳定结论。
- `user_updates` 聚焦用户的稳定事实、偏好、目标、边界与长期沟通习惯。
- `soul_updates` 聚焦主脑的稳定风格、表达原则与长期策略修正。
- 不要输出标题、编号、解释前缀或多段内容，每一项都用单句表达。

最近的认知事件：
{event_block}

返回结构必须符合：
{{
  "summary": "",
  "memory_candidates": [
    {{
      "audience": "brain|task|shared",
      "kind": "episodic|durable|procedural",
      "type": "insight|user|preference|workflow|skill",
      "summary": "",
      "content": "",
      "importance": 1,
      "confidence": 0.0,
      "stability": 0.0,
      "tags": [""],
      "payload": {{}}
    }}
  ],
  "user_updates": [""],
  "soul_updates": [""],
  "skill_hints": [
    {{
      "summary": "",
      "content": "",
      "trigger": "",
      "hint": "",
      "skill_name": ""
    }}
  ]
}}

字段说明：
- `summary`：这一阶段的高层总结。
- `memory_candidates`：真正值得进入统一长期记忆的候选列表，没有就返回空数组。
- `user_updates`：对用户整体画像的更新候选，没有就返回空数组；每一项都应像 `用户更喜欢先讨论架构，再进入实现细节。` 这样可直接落盘。
- `soul_updates`：对主脑稳定风格的更新候选，没有就返回空数组；每一项都应像 `复杂任务中先收敛判断，再交给 task 执行。` 这样可直接落盘。
- `skill_hints`：只有在重复模式明显可复用时才填写，没有就返回空数组。

`skill_hints` 字段说明：
- `summary`：一句话概括这个技能提示。
- `content`：更完整的说明。
- `trigger`：什么情况下应触发。
- `hint`：给 task 的紧凑提示。
- `skill_name`：未来沉淀为技能时的名称。

示例：
{{
  "summary": "近期多轮任务显示，复杂问题更适合由 task 内部收敛后再交回主脑。",
  "memory_candidates": [
    {{
      "audience": "task",
      "kind": "procedural",
      "type": "workflow",
      "summary": "复杂任务适合走最终结果式执行链路",
      "content": "当任务需要多步分析和工具配合时，task 应优先在内部收敛，再把最终结果返回给 brain。",
      "importance": 8,
      "confidence": 0.88,
      "stability": 0.81,
      "tags": ["workflow", "task"],
      "payload": {{
        "goal_cluster": "complex_execution",
        "tool_sequence": ["analysis", "tool", "summary"],
        "preconditions": ["需要多步执行"],
        "steps_summary": "主脑决策，task 内部收敛后返回最终结果",
        "sample_size": 4,
        "success_rate": 0.8
      }}
    }}
  ],
  "user_updates": [],
  "soul_updates": [],
  "skill_hints": [
    {{
      "summary": "复杂任务优先走最终结果式执行",
      "content": "对于复杂任务，优先让 task 在单次执行中收敛到最终结果。",
      "trigger": "需要多步执行或工具组合时",
      "hint": "减少中间汇报，优先给最终结果。",
      "skill_name": "final-result-execution"
    }}
  ]
}}
""".strip()

    def __init__(self, llm: Any):
        self.llm = llm

    async def propose(self, events: list[dict[str, Any]]) -> DeepReflectionProposal:
        if not events:
            return DeepReflectionProposal()

        fallback = self._fallback_payload(events)
        if not self.llm:
            return self._proposal_from_payload(fallback)

        prompt = self._PROMPT.format(event_block=self._build_event_block(events))
        try:
            structured_llm = self.llm.with_structured_output(DeepReflectionOutput)
            parsed = await structured_llm.ainvoke(prompt)
        except Exception:
            parsed = None

        payload = self._normalize_payload(parsed if isinstance(parsed, dict) else fallback)
        if not payload["memory_candidates"] and fallback["memory_candidates"]:
            payload = fallback
        return self._proposal_from_payload(payload)

    @staticmethod
    def _proposal_from_payload(payload: dict[str, Any]) -> DeepReflectionProposal:
        return DeepReflectionProposal(
            summary=str(payload.get("summary", "") or "").strip(),
            memory_candidates=[
                *list(payload.get("memory_candidates", []) or []),
                *DeepReflectionService._normalize_skill_hints(payload.get("skill_hints")),
            ],
            user_updates=DeepReflectionService._normalize_str_list(payload.get("user_updates")),
            soul_updates=DeepReflectionService._normalize_str_list(payload.get("soul_updates")),
        )


    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidates = self._normalize_candidates(payload.get("memory_candidates"))
        skill_hints = self._normalize_skill_hints(payload.get("skill_hints"))
        return {
            "summary": str(payload.get("summary", "") or "").strip(),
            "memory_candidates": [*candidates, *skill_hints],
            "user_updates": self._normalize_str_list(payload.get("user_updates")),
            "soul_updates": self._normalize_str_list(payload.get("soul_updates")),
        }

    def _fallback_payload(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        skill_hints: list[dict[str, Any]] = []

        tool_events = [event for event in events if (event.get("task") or {}).get("used")]
        if len(tool_events) >= 2:
            candidates.append(
                {
                    "audience": "task",
                    "kind": "procedural",
                    "type": "workflow",
                    "summary": "近期多轮任务中持续使用执行链路解决问题。",
                    "content": "最近多轮任务都依赖 task 执行并由 brain 统一收口，适合继续保持最终结果式返回。",
                    "importance": 7,
                    "confidence": 0.76,
                    "stability": 0.68,
                    "tags": ["workflow", "task"],
                    "payload": {
                        "goal_cluster": "general_execution",
                        "tool_sequence": [],
                        "preconditions": ["需要外部工具或多步执行"],
                        "steps_summary": "主脑决策，task 完成执行并返回最终结果。",
                        "sample_size": len(tool_events),
                        "success_rate": 0.7,
                    },
                }
            )
            skill_hints.append(
                {
                    "summary": "复杂任务默认走最终结果式执行链路",
                    "content": "遇到复杂任务时，task 优先在单次执行内完成收敛，再把最终结果交回 brain。",
                    "trigger": "需要多步执行或工具组合时",
                    "hint": "减少中间态汇报，优先收敛到最终结果。",
                    "skill_name": "final-result-execution",
                }
            )

        return {
            "summary": "已对近期多轮认知事件完成一次深反思。",
            "memory_candidates": candidates,
            "user_updates": [],
            "soul_updates": [],
            "skill_hints": skill_hints,
        }

    @staticmethod
    def _build_event_block(events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for event in events:
            event_id = str(event.get("id", "") or "")
            timestamp = str(event.get("timestamp", "") or "")[:19]
            user_input = str(event.get("user_input", "") or "").strip()
            assistant_output = str(event.get("assistant_output", "") or "").strip()
            turn_reflection = event.get("turn_reflection") if isinstance(event.get("turn_reflection"), dict) else {}
            brain_state = event.get("brain_state") if isinstance(event.get("brain_state"), dict) else {}
            task = event.get("task") if isinstance(event.get("task"), dict) else {}
            lifecycle_status = str(task.get("status", "none") or "none").strip()
            result_status = str(task.get("result_status", "") or "").strip()
            execution_status = lifecycle_status
            if result_status:
                execution_status = f"{lifecycle_status}/{result_status}"
            problems = DeepReflectionService._normalize_str_list(turn_reflection.get("problems"))
            user_updates = DeepReflectionService._normalize_str_list(turn_reflection.get("user_updates"))
            soul_updates = DeepReflectionService._normalize_str_list(turn_reflection.get("soul_updates"))
            state_update = turn_reflection.get("state_update") if isinstance(turn_reflection.get("state_update"), dict) else {}
            emotion_label = str(brain_state.get("emotion", "") or "").strip()
            pad = dict(brain_state.get("pad", {}) or {})
            drives = dict(brain_state.get("drives", {}) or {})
            lines.append(
                "- "
                f"{event_id} [{timestamp}] 用户={DeepReflectionService._compact(user_input, 80)} "
                f"主脑回复={DeepReflectionService._compact(assistant_output, 80)} "
                f"反思摘要={DeepReflectionService._compact(str(turn_reflection.get('summary', '') or ''), 80)} "
                f"执行状态={execution_status} "
                f"emotion={emotion_label or 'unknown'} "
                f"pad={json.dumps(pad, ensure_ascii=False, sort_keys=True)} "
                f"drives={json.dumps(drives, ensure_ascii=False, sort_keys=True)} "
                f"problems={json.dumps(problems, ensure_ascii=False)} "
                f"user_updates={json.dumps(user_updates, ensure_ascii=False)} "
                f"soul_updates={json.dumps(soul_updates, ensure_ascii=False)} "
                f"state_update={json.dumps(state_update, ensure_ascii=False, sort_keys=True)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _normalize_candidates(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        records: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            if not summary and not content:
                continue
            records.append(
                {
                    "audience": str(item.get("audience", "shared") or "shared").strip(),
                    "kind": str(item.get("kind", "durable") or "durable").strip(),
                    "type": str(item.get("type", "insight") or "insight").strip(),
                    "summary": summary or DeepReflectionService._compact(content, 120),
                    "content": content or summary,
                    "importance": int(item.get("importance", 6) or 6),
                    "confidence": float(item.get("confidence", 0.78) or 0.78),
                    "stability": float(item.get("stability", 0.72) or 0.72),
                    "tags": DeepReflectionService._normalize_str_list(item.get("tags")),
                    "payload": dict(item.get("payload", {}) or {}),
                }
            )
        return records[:8]

    @staticmethod
    def _normalize_skill_hints(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        records: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            hint = str(item.get("hint", "") or "").strip()
            trigger = str(item.get("trigger", "") or "").strip()
            if not any((summary, content, hint, trigger)):
                continue
            skill_name = str(item.get("skill_name", "") or "").strip() or "unnamed-skill"
            records.append(
                {
                    "audience": "task",
                    "kind": "procedural",
                    "type": "skill_hint",
                    "summary": summary or DeepReflectionService._compact(content or hint, 120),
                    "content": content or hint or summary,
                    "importance": 7,
                    "confidence": 0.8,
                    "stability": 0.85,
                    "tags": ["skill", "hint"],
                    "payload": {
                        "skill_id": f"skill_{re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '_', skill_name.lower()).strip('_') or 'hint'}",
                        "skill_name": skill_name,
                        "trigger": trigger,
                        "hint": hint or content or summary,
                        "applies_to_tools": [],
                    },
                }
            )
        return records[:4]

    @staticmethod
    def _normalize_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items[:6]

    @staticmethod
    def _compact(text: str, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

__all__ = ["DeepReflectionProposal", "DeepReflectionResult", "DeepReflectionService"]
