"""Per-turn reflection aligned with the new architecture."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.utils.llm_utils import extract_message_text


@dataclass(frozen=True)
class TurnReflectionResult:
    turn_reflection: dict[str, Any]
    state_snapshot: dict[str, Any] | None = None


class TurnReflectionService:
    """Generate one compact per-turn reflection plus memory candidates."""

    _PROMPT = """
你是 `brain` 的逐轮反思环节。

只返回 JSON，不要输出任何额外说明。

任务：
1. 总结本轮发生了什么。
2. 列出本轮暴露出的主要问题（如果有）。
3. 说明问题最终是如何解决的。
4. 从 `brain` 视角评价本轮 `task` 执行情况。
5. 只在确实有长期价值时，产出少量长期记忆候选。
6. 只在本轮存在高置信、可直接落盘的信息时，填写 `user_updates` 与 `soul_updates`。
7. 只在确实需要微调当前实时状态时，填写 `state_update`，并且只能给出很小的增量。

规则：
- `memory_candidates` 必须简洁，并且足够稳定，能帮助未来轮次。
- `user_updates` / `soul_updates` 的每一项都必须是可直接写入 Markdown 列表的单句结论。
- `user_updates` 聚焦用户本轮明确表达出的稳定事实、偏好、目标、边界或协作习惯。
- `soul_updates` 聚焦主脑本轮需要立即记住的表达方式、风格要求或协作策略修正。
- `state_update` 只能输出很小的 PAD / drive 增量，不要重写整个状态，不要输出大幅度变化。
- 不要复制原始日志，不要复制大段对话原文。
- 如果本轮没有执行，`outcome` 设为 `no_execution`，并把 `execution_review.effectiveness` 设为 `none`。
- 如果本轮发生了执行，只描述最关键的阻塞、尝试过程和最有价值的下一步提示。
- `summary`、`resolution`、`next_hint` 使用与用户相同的语言。
- 没有内容时，字符串字段返回 `""`，数组字段返回 `[]`，对象字段返回 `{}`。

本轮上下文：
- user_input: {user_input}
- assistant_output: {output}
- emotion: {emotion_label}
- pad: {pad_json}
- drives: {drives_json}
- execution: {execution_json}

返回结构必须符合：
{{
  "summary": "",
  "problems": [""],
  "resolution": "",
  "outcome": "success|partial|failed|no_execution",
  "next_hint": "",
  "user_updates": [""],
  "soul_updates": [""],
  "state_update": {{
    "should_apply": false,
    "confidence": 0.0,
    "reason": "",
    "pad_delta": {{
      "pleasure": 0.0,
      "arousal": 0.0,
      "dominance": 0.0
    }},
    "drives_delta": {{
      "social": 0.0,
      "energy": 0.0
    }}
  }},
  "memory_candidates": [
    {{
      "audience": "brain|task|shared",
      "kind": "episodic|durable|procedural",
      "type": "turn_insight|user_fact|preference|goal|constraint|relationship|soul_trait|tool_experience|error_pattern|workflow_pattern|skill_hint",
      "summary": "",
      "content": "",
      "importance": 1,
      "confidence": 0.0,
      "stability": 0.0,
      "tags": [""],
      "payload": {{}}
    }}
  ],
  "execution_review": {{
    "attempt_count": 0,
    "effectiveness": "high|medium|low|none",
    "main_failure_reason": "",
    "missing_inputs": [""],
    "next_execution_hint": ""
  }}
}}

字段说明：
- `summary`：本轮最核心的总结。
- `problems`：本轮暴露出来的问题列表，没有就返回空数组。
- `resolution`：这些问题最终是如何被解决的。
- `outcome`：只能是 `success`、`partial`、`failed`、`no_execution`。
- `next_hint`：下一轮主脑最值得记住的承接提示。
- `user_updates`：本轮新增的用户信息直写候选，没有就返回空数组。
- `soul_updates`：本轮新增的主脑风格修正直写候选，没有就返回空数组。
- `state_update`：对 `current_state.md` 的小幅增量更新建议；没有就保留 `should_apply=false` 且增量为空对象。
- `memory_candidates`：确实值得写入长期记忆的候选，没有就返回空数组。
- `execution_review`：对执行过程的紧凑评价。

`state_update` 内部字段说明：
- `should_apply`：是否建议应用这次实时状态增量。
- `confidence`：0 到 1 的小数，只有高置信才应为 true。
- `reason`：为什么要更新当前实时状态。
- `pad_delta`：PAD 三个维度的微小增量，没有就 `{}`。
- `drives_delta`：`social` / `energy` 的微小增量，没有就 `{}`。

`memory_candidates` 内部字段说明：
- `audience`：只能是 `brain`、`task`、`shared`。
- `kind`：只能是 `episodic`、`durable`、`procedural`。
- `type`：只能从给定枚举里选。
- `summary`：一句话摘要。
- `content`：蒸馏后的完整内容。
- `importance`：1 到 10 的整数。
- `confidence`：0 到 1 的小数。
- `stability`：0 到 1 的小数。
- `tags`：标签列表，没有就 `[]`。
- `payload`：类型扩展字段，没有就 `{}`。

示例：
{{
  "summary": "本轮围绕一个执行问题进行了多次尝试，最终得到可用结果。",
  "problems": ["初始方案缺少关键参数"],
  "resolution": "调整执行路径后完成了任务。",
  "outcome": "success",
  "next_hint": "下次遇到类似情况时先确认关键参数是否齐全。",
  "user_updates": ["用户希望复杂问题先收敛架构判断，再进入具体实现。"],
  "soul_updates": ["复杂问题先收敛判断，再把最终任务交给 task。"],
  "state_update": {{
    "should_apply": true,
    "confidence": 0.72,
    "reason": "本轮问题得到解决，当前状态可以小幅提升稳定感与掌控感。",
    "pad_delta": {{
      "pleasure": 0.06,
      "arousal": -0.03,
      "dominance": 0.08
    }},
    "drives_delta": {{
      "energy": -2.0
    }}
  }},
  "memory_candidates": [
    {{
      "audience": "shared",
      "kind": "episodic",
      "type": "turn_insight",
      "summary": "多次尝试后完成执行",
      "content": "本轮在补齐关键条件后完成执行，后续可优先检查缺参。",
      "importance": 7,
      "confidence": 0.86,
      "stability": 0.52,
      "tags": ["execution", "retry"],
      "payload": {{
        "problem": "初始方案缺少关键参数",
        "attempt_count": 2,
        "resolution": "补齐条件后成功执行",
        "outcome": "success",
        "follow_up": "下次先检查关键参数"
      }}
    }}
  ],
  "execution_review": {{
    "attempt_count": 2,
    "effectiveness": "high",
    "main_failure_reason": "",
    "missing_inputs": [],
    "next_execution_hint": ""
  }}
}}
""".strip()

    def __init__(self, emotion_manager: EmotionStateManager, llm: Any):
        self.emotion_mgr = emotion_manager
        self.llm = llm

    async def reflect_turn(
        self,
        *,
        user_input: str,
        output: str,
        emotion_label: str,
        pad: dict[str, float],
        drives: dict[str, float],
        execution: dict[str, Any] | None = None,
    ) -> TurnReflectionResult:
        normalized_execution = self._normalize_execution(execution)
        if not self.llm:
            reflection = self._fallback_turn_reflection(
                user_input=user_input,
                output=output,
                execution=normalized_execution,
            )
            return TurnReflectionResult(turn_reflection=reflection, state_snapshot=self.emotion_mgr.snapshot())

        prompt = self._PROMPT.format(
            user_input=user_input,
            output=output,
            emotion_label=emotion_label,
            pad_json=json.dumps(pad, ensure_ascii=False),
            drives_json=json.dumps(drives, ensure_ascii=False),
            execution_json=json.dumps(normalized_execution, ensure_ascii=False),
        )
        try:
            response = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            parsed = self._extract_json(extract_message_text(response))
        except Exception:
            parsed = None

        reflection = self._normalize_turn_reflection(
            parsed if isinstance(parsed, dict) else {},
            user_input=user_input,
            output=output,
            execution=normalized_execution,
        )
        return TurnReflectionResult(turn_reflection=reflection, state_snapshot=self.emotion_mgr.snapshot())

    def _normalize_turn_reflection(
        self,
        payload: dict[str, Any],
        *,
        user_input: str,
        output: str,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_turn_reflection(
            user_input=user_input,
            output=output,
            execution=execution,
        )
        normalized = {
            "summary": str(payload.get("summary", "") or fallback["summary"]).strip(),
            "problems": self._normalize_str_list(payload.get("problems")) or fallback["problems"],
            "resolution": str(payload.get("resolution", "") or fallback["resolution"]).strip(),
            "outcome": self._normalize_outcome(payload.get("outcome"), fallback["outcome"]),
            "next_hint": str(payload.get("next_hint", "") or fallback["next_hint"]).strip(),
            "user_updates": self._normalize_str_list(payload.get("user_updates")),
            "soul_updates": self._normalize_str_list(payload.get("soul_updates")),
            "state_update": self._normalize_state_update(payload.get("state_update")),
            "memory_candidates": self._normalize_memory_candidates(payload.get("memory_candidates")) or fallback["memory_candidates"],
            "execution_review": self._normalize_execution_review(
                payload.get("execution_review"),
                fallback=fallback["execution_review"],
            ),
        }
        return normalized

    def _fallback_turn_reflection(
        self,
        *,
        user_input: str,
        output: str,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        invoked = bool(execution.get("invoked"))
        status = str(execution.get("status", "none") or "none")
        missing = self._normalize_str_list(execution.get("missing"))
        summary = self._compact(user_input or output, limit=90) or "本轮完成了一次正常对话。"
        if invoked and status == "done":
            resolution = "执行已完成，并返回了可用结果。"
            outcome = "success"
        elif invoked and status == "failed":
            resolution = "执行未完成，需要调整做法。"
            outcome = "failed"
        elif invoked:
            resolution = "执行已有进展，但还缺少继续推进的条件。"
            outcome = "partial"
        else:
            resolution = "主脑直接完成了本轮回复。"
            outcome = "no_execution"

        problems = []
        if missing:
            problems.append("缺少继续执行所需的信息")
        failure_reason = str(execution.get("failure_reason", "") or "").strip()
        if failure_reason:
            problems.append(failure_reason)

        execution_review = {
            "attempt_count": int(execution.get("attempt_count", 1 if invoked else 0) or 0),
            "effectiveness": self._fallback_effectiveness(execution),
            "main_failure_reason": failure_reason,
            "missing_inputs": missing,
            "next_execution_hint": "先补齐缺失信息，再继续执行。" if missing else "",
        }

        memory_candidates: list[dict[str, Any]] = []
        if invoked:
            memory_candidates.append(
                {
                    "audience": "shared",
                    "kind": "episodic",
                    "type": "turn_insight",
                    "summary": self._compact(summary, limit=100),
                    "content": self._compact(
                        f"本轮执行状态为 {status}。{resolution}"
                        + (f" 主要缺失信息：{'; '.join(missing[:3])}。" if missing else ""),
                        limit=220,
                    ),
                    "importance": 7 if status in {"failed", "need_more"} else 6,
                    "confidence": 0.82,
                    "stability": 0.45,
                    "tags": ["turn", "execution"],
                    "payload": {
                        "problem": problems[0] if problems else "",
                        "attempt_count": execution_review["attempt_count"],
                        "resolution": resolution,
                        "outcome": outcome,
                        "follow_up": execution_review["next_execution_hint"],
                    },
                }
            )

        return {
            "summary": summary,
            "problems": problems,
            "resolution": resolution,
            "outcome": outcome,
            "next_hint": "承接本轮结果继续推进。" if invoked else "自然承接用户当前话题。",
            "user_updates": [],
            "soul_updates": [],
            "state_update": {
                "should_apply": False,
                "confidence": 0.0,
                "reason": "",
                "pad_delta": {},
                "drives_delta": {},
            },
            "memory_candidates": memory_candidates,
            "execution_review": execution_review,
        }

    @staticmethod
    def _normalize_execution(execution: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(execution or {})
        control_state = str(payload.get("control_state", "idle") or "idle").strip().lower()
        status = str(payload.get("status", "none") or "none").strip().lower()
        return {
            "invoked": bool(payload.get("invoked")) or any(
                [
                    str(payload.get("thread_id", "") or "").strip(),
                    str(payload.get("run_id", "") or "").strip(),
                    str(payload.get("summary", "") or "").strip(),
                ]
            ),
            "thread_id": str(payload.get("thread_id", "") or "").strip(),
            "run_id": str(payload.get("run_id", "") or "").strip(),
            "control_state": control_state if control_state in {"idle", "running", "paused", "stopped", "completed"} else "idle",
            "status": status if status in {"none", "done", "need_more", "failed"} else "none",
            "summary": str(payload.get("summary", "") or "").strip(),
            "missing": TurnReflectionService._normalize_str_list(payload.get("missing")),
            "pending_review": dict(payload.get("pending_review", {}) or {}),
            "recommended_action": str(payload.get("recommended_action", "") or "").strip(),
            "confidence": float(payload.get("confidence", 0.0) or 0.0),
            "attempt_count": int(payload.get("attempt_count", 0) or 0),
            "failure_reason": str(payload.get("failure_reason", "") or "").strip(),
        }

    @staticmethod
    def _normalize_execution_review(payload: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
        review = payload if isinstance(payload, dict) else {}
        return {
            "attempt_count": int(review.get("attempt_count", fallback.get("attempt_count", 0)) or 0),
            "effectiveness": TurnReflectionService._normalize_effectiveness(
                review.get("effectiveness"),
                default=str(fallback.get("effectiveness", "none") or "none"),
            ),
            "main_failure_reason": str(review.get("main_failure_reason", fallback.get("main_failure_reason", "")) or "").strip(),
            "missing_inputs": TurnReflectionService._normalize_str_list(review.get("missing_inputs"))
            or TurnReflectionService._normalize_str_list(fallback.get("missing_inputs")),
            "next_execution_hint": str(review.get("next_execution_hint", fallback.get("next_execution_hint", "")) or "").strip(),
        }

    @staticmethod
    def _normalize_memory_candidates(value: Any) -> list[dict[str, Any]]:
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
                    "kind": str(item.get("kind", "episodic") or "episodic").strip(),
                    "type": str(item.get("type", "turn_insight") or "turn_insight").strip(),
                    "summary": summary,
                    "content": content or summary,
                    "importance": int(item.get("importance", 5) or 5),
                    "confidence": float(item.get("confidence", 0.8) or 0.8),
                    "stability": float(item.get("stability", 0.5) or 0.5),
                    "tags": TurnReflectionService._normalize_str_list(item.get("tags")),
                    "payload": dict(item.get("payload", {}) or {}),
                }
            )
        return records[:6]

    @staticmethod
    def _normalize_state_update(value: Any) -> dict[str, Any]:
        payload = value if isinstance(value, dict) else {}
        pad_delta = TurnReflectionService._normalize_delta_map(
            payload.get("pad_delta"),
            allowed=("pleasure", "arousal", "dominance"),
            max_abs=0.3,
        )
        drives_delta = TurnReflectionService._normalize_delta_map(
            payload.get("drives_delta"),
            allowed=("social", "energy"),
            max_abs=20.0,
        )
        should_apply = bool(payload.get("should_apply", False)) or bool(pad_delta or drives_delta)
        return {
            "should_apply": should_apply,
            "confidence": TurnReflectionService._clamp_float(
                payload.get("confidence"),
                default=0.0,
                minimum=0.0,
                maximum=1.0,
            ),
            "reason": str(payload.get("reason", "") or "").strip(),
            "pad_delta": pad_delta,
            "drives_delta": drives_delta,
        }

    @staticmethod
    def _normalize_delta_map(
        payload: Any,
        *,
        allowed: tuple[str, ...],
        max_abs: float,
    ) -> dict[str, float]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, float] = {}
        precision = 3 if max_abs <= 1.0 else 2
        for key in allowed:
            if key not in payload:
                continue
            try:
                value = float(payload.get(key, 0.0) or 0.0)
            except Exception:
                continue
            value = max(-max_abs, min(max_abs, value))
            if abs(value) > 1e-6:
                normalized[key] = round(value, precision)
        return normalized

    @staticmethod
    def _normalize_outcome(value: Any, default: str) -> str:
        outcome = str(value or default).strip().lower()
        return outcome if outcome in {"success", "partial", "failed", "no_execution"} else default

    @staticmethod
    def _normalize_effectiveness(value: Any, *, default: str) -> str:
        effectiveness = str(value or default).strip().lower()
        return effectiveness if effectiveness in {"high", "medium", "low", "none"} else default

    @staticmethod
    def _fallback_effectiveness(execution: dict[str, Any]) -> str:
        if not execution.get("invoked"):
            return "none"
        status = str(execution.get("status", "none") or "none")
        if status == "done":
            return "high"
        if status == "failed":
            return "low"
        return "medium"

    @staticmethod
    def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
        try:
            numeric = float(value)
        except Exception:
            numeric = default
        return max(minimum, min(maximum, numeric))

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
    def _compact(text: str, *, limit: int) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


__all__ = ["TurnReflectionResult", "TurnReflectionService"]

