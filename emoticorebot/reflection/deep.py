"""Proposal-only deep reflection service."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from emoticorebot.reflection.candidates import (
    compact_text,
    normalize_memory_candidates,
)
from emoticorebot.types import DeepReflectionOutput
from emoticorebot.utils.llm_utils import extract_message_text


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


class DeepReflectionUnavailable(RuntimeError):
    """Raised when deep reflection cannot be generated from a real model result."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason or "").strip() or "deep_reflection_unavailable"


class DeepReflectionService:
    """Consolidate recent cognitive events into a structured deep-reflection proposal."""

    _JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

    _PROMPT = """
你是 `left_brain` 的深反思过程。

请严格按结构化字段填写，不要补充额外说明。

任务：
1. 阅读最近的 `cognitive_event`。
2. 只提炼真正稳定的长期价值。
3. 为统一长期记忆存储产出 `memory_candidates`。
4. 如果存在可复用执行模式，直接把它写成正式长期记忆候选，不要再单独输出其他中间结构。

规则：
- 不要复制原始日志或大段文本。
- 优先给出稳定结论，而不是一次性噪声。
- 如果证据不足，直接返回空列表。
        - 没有内容时，字符串字段返回 `""`，数组字段返回 `[]`，对象字段返回 `{{}}`。
- `user_updates` / `soul_updates` 的每一项都必须是一条可直接写入 Markdown 列表的稳定结论。
- `user_updates` 聚焦用户的稳定事实、偏好、目标、边界与长期沟通习惯。
- `soul_updates` 聚焦左脑的稳定风格、表达原则与长期策略修正。
- 不要输出标题、编号、解释前缀或多段内容，每一项都用单句表达。

最近的认知事件：
{event_block}

返回结构必须符合：
{{
  "summary": "",
  "memory_candidates": [
    {{
      "memory_type": "relationship|fact|working|execution|reflection",
      "summary": "",
      "detail": "",
      "confidence": 0.0,
      "stability": 0.0,
      "tags": [""],
      "metadata": {{
        "subtype": "",
        "importance": 1
      }}
    }}
  ],
  "user_updates": [""],
  "soul_updates": [""]
}}

字段说明：
- `summary`：这一阶段的高层总结。
- `memory_candidates`：真正值得进入统一长期记忆的候选列表，没有就返回空数组。
- `user_updates`：对用户整体画像的更新候选，没有就返回空数组；每一项都应像 `用户更喜欢先讨论架构，再进入实现细节。` 这样可直接落盘。
- `soul_updates`：对左脑稳定风格的更新候选，没有就返回空数组；每一项都应像 `复杂任务中先收敛判断，再交给 task 执行。` 这样可直接落盘。
- 如果需要表达可复用执行技能，请直接输出 `memory_type="execution"` 的候选，并在 `metadata.subtype` 里写 `skill_hint`，相关 `skill_name / trigger / hint` 也放进 `metadata`。

示例：
{{
  "summary": "近期多轮任务显示，复杂问题更适合由 task 内部收敛后再交回左脑。",
  "memory_candidates": [
    {{
      "memory_type": "execution",
      "summary": "复杂任务适合走最终结果式执行链路",
      "detail": "当任务需要多步分析和工具配合时，task 应优先在内部收敛，再把最终结果返回给 left_brain。",
      "confidence": 0.88,
      "stability": 0.81,
      "tags": ["workflow", "task"],
      "metadata": {{
        "subtype": "workflow",
        "importance": 8,
        "goal_cluster": "complex_execution",
        "tool_sequence": ["analysis", "tool", "summary"],
        "preconditions": ["需要多步执行"],
        "steps_summary": "左脑决策，task 内部收敛后返回最终结果",
        "sample_size": 4,
        "success_rate": 0.8
      }}
    }},
    {{
      "memory_type": "execution",
      "summary": "复杂任务优先走最终结果式执行",
      "detail": "对于复杂任务，优先让 task 在单次执行中收敛到最终结果。",
      "confidence": 0.8,
      "stability": 0.85,
      "tags": ["skill", "hint"],
      "metadata": {{
        "subtype": "skill_hint",
        "importance": 7,
        "skill_name": "final-result-execution",
        "skill_id": "skill_final_result_execution",
        "trigger": "需要多步执行或工具组合时",
        "hint": "减少中间汇报，优先给最终结果。",
        "applies_to_tools": []
      }}
    }}
  ],
  "user_updates": [],
  "soul_updates": []
}}
""".strip()

    def __init__(self, llm: Any):
        self.llm = llm

    async def propose(self, events: list[dict[str, Any]]) -> DeepReflectionProposal:
        if not events:
            return DeepReflectionProposal()

        if not self.llm:
            raise DeepReflectionUnavailable("deep_reflection_llm_unavailable")

        prompt = self._PROMPT.format(event_block=self._build_event_block(events))
        payload = self._normalize_payload(await self._invoke_reflection_model(prompt))
        return self._proposal_from_payload(payload)

    @staticmethod
    def _proposal_from_payload(payload: dict[str, Any]) -> DeepReflectionProposal:
        return DeepReflectionProposal(
            summary=str(payload.get("summary", "") or "").strip(),
            memory_candidates=list(payload.get("memory_candidates", []) or []),
            user_updates=DeepReflectionService._normalize_str_list(payload.get("user_updates")),
            soul_updates=DeepReflectionService._normalize_str_list(payload.get("soul_updates")),
        )

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "summary": str(payload.get("summary", "") or "").strip(),
            "memory_candidates": normalize_memory_candidates(
                payload.get("memory_candidates"),
                default_memory_type="fact",
                default_confidence=0.78,
                default_stability=0.72,
                default_importance=6,
                limit=8,
            ),
            "user_updates": self._normalize_str_list(payload.get("user_updates")),
            "soul_updates": self._normalize_str_list(payload.get("soul_updates")),
        }

    async def _invoke_reflection_model(self, prompt: str) -> dict[str, Any]:
        structured_error: Exception | None = None
        if hasattr(self.llm, "with_structured_output"):
            try:
                structured_llm = self.llm.with_structured_output(DeepReflectionOutput)
                if hasattr(structured_llm, "ainvoke"):
                    response = await structured_llm.ainvoke(prompt)
                elif hasattr(structured_llm, "invoke"):
                    response = structured_llm.invoke(prompt)
                else:
                    response = None
                payload = self._coerce_payload(response)
                if payload:
                    return payload
            except Exception as exc:
                structured_error = exc

        try:
            if hasattr(self.llm, "ainvoke"):
                response = await self.llm.ainvoke(prompt)
            elif hasattr(self.llm, "invoke"):
                response = self.llm.invoke(prompt)
            else:
                raise DeepReflectionUnavailable("deep_reflection_llm_missing_invoke")
            payload = self._parse_json_payload(extract_message_text(response))
            if payload:
                return payload
        except DeepReflectionUnavailable:
            raise
        except Exception as exc:
            if structured_error is None:
                structured_error = exc

        raise DeepReflectionUnavailable(
            "deep_reflection_generation_failed"
            + (f": {type(structured_error).__name__}" if structured_error is not None else "")
        )

    @classmethod
    def _coerce_payload(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        if hasattr(value, "dict"):
            dumped = value.dict()
            if isinstance(dumped, Mapping):
                return dict(dumped)
        if isinstance(value, str):
            return cls._parse_json_payload(value)
        return {}

    @classmethod
    def _parse_json_payload(cls, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        candidates = [raw]
        match = cls._JSON_FENCE_RE.search(raw)
        if match:
            candidates.insert(0, match.group(1).strip())
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            candidates.append(raw[start : end + 1])
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, Mapping):
                return dict(payload)
        return {}

    @staticmethod
    def _build_event_block(events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for event in events:
            event_id = str(event.get("id", "") or "")
            timestamp = str(event.get("timestamp", "") or "")[:19]
            user_input = str(event.get("user_input", "") or "").strip()
            assistant_output = str(event.get("assistant_output", "") or "").strip()
            turn_reflection = event.get("turn_reflection") if isinstance(event.get("turn_reflection"), dict) else {}
            left_brain_state = event.get("left_brain_state") if isinstance(event.get("left_brain_state"), dict) else {}
            task = event.get("task") if isinstance(event.get("task"), dict) else {}
            lifecycle_status = str(task.get("state", "none") or "none").strip()
            result_status = str(task.get("result", "") or "").strip()
            execution_status = lifecycle_status
            if result_status and result_status != "none":
                execution_status = f"{lifecycle_status}/{result_status}"
            problems = DeepReflectionService._normalize_str_list(turn_reflection.get("problems"))
            user_updates = DeepReflectionService._normalize_str_list(turn_reflection.get("user_updates"))
            soul_updates = DeepReflectionService._normalize_str_list(turn_reflection.get("soul_updates"))
            needs_deep_reflection = bool(turn_reflection.get("needs_deep_reflection", False))
            state_update = turn_reflection.get("state_update") if isinstance(turn_reflection.get("state_update"), dict) else {}
            emotion_label = str(left_brain_state.get("emotion", "") or "").strip()
            pad = dict(left_brain_state.get("pad", {}) or {})
            drives = dict(left_brain_state.get("drives", {}) or {})
            lines.append(
                "- "
                f"{event_id} [{timestamp}] 用户={DeepReflectionService._compact(user_input, 80)} "
                f"左脑回复={DeepReflectionService._compact(assistant_output, 80)} "
                f"反思摘要={DeepReflectionService._compact(str(turn_reflection.get('summary', '') or ''), 80)} "
                f"执行状态={execution_status} "
                f"emotion={emotion_label or 'unknown'} "
                f"pad={json.dumps(pad, ensure_ascii=False, sort_keys=True)} "
                f"drives={json.dumps(drives, ensure_ascii=False, sort_keys=True)} "
                f"problems={json.dumps(problems, ensure_ascii=False)} "
                f"user_updates={json.dumps(user_updates, ensure_ascii=False)} "
                f"soul_updates={json.dumps(soul_updates, ensure_ascii=False)} "
                f"needs_deep_reflection={json.dumps(needs_deep_reflection, ensure_ascii=False)} "
                f"state_update={json.dumps(state_update, ensure_ascii=False, sort_keys=True)}"
            )
        return "\n".join(lines)

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
        return compact_text(text, limit=limit)

__all__ = [
    "DeepReflectionProposal",
    "DeepReflectionResult",
    "DeepReflectionService",
    "DeepReflectionUnavailable",
]
