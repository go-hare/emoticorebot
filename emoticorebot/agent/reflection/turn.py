"""Per-turn reflection aligned with the new architecture."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from emoticorebot.agent.reflection.memory_candidates import compact_text, normalize_memory_candidates
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.types import EmotionState, ExecutionInfo, TurnReflectionOutput


@dataclass(frozen=True)
class TurnReflectionResult:
    """逐轮反思结果"""
    turn_reflection: TurnReflectionOutput  # 反思输出
    state_snapshot: dict[str, Any] | None = None  # 情绪状态快照


class TurnReflectionService:
    """Generate one compact per-turn reflection plus memory candidates."""

    _PROMPT = """
你是 `brain` 的逐轮反思环节。

请严格按结构化字段填写，不要补充额外说明。

任务：
1. 总结本轮发生了什么。
2. 列出本轮暴露出的主要问题（如果有）。
3. 说明问题最终是如何解决的。
4. 从 `brain` 视角评价本轮 `task` 执行情况。
5. 只在确实有长期价值时，产出少量长期记忆候选。
6. 只在本轮存在高置信、可直接落盘的信息时，填写 `user_updates` 与 `soul_updates`。
7. 必须填写 `state_update`，并且 `pad_delta` / `drives_delta` 不能返回空对象。

规则：
- `memory_candidates` 必须简洁，并且足够稳定，能帮助未来轮次。
- `user_updates` / `soul_updates` 的每一项都必须是可直接写入 Markdown 列表的单句结论。
- `user_updates` 聚焦用户本轮明确表达出的稳定事实、偏好、目标、边界或协作习惯。
- `soul_updates` 聚焦主脑本轮需要立即记住的表达方式、风格要求或协作策略修正。
- `state_update` 必须始终填写。
- `pad_delta` 必须始终包含 `pleasure`、`arousal`、`dominance` 三个键。
- `drives_delta` 必须始终包含 `social`、`energy` 两个键。
- 字段名虽然叫 `pad_delta` / `drives_delta`，但在这里必须填写“你判断后的状态值”，不要填写增量、差值或 `+0.1` / `-2.0` 这种微调量。
- 如果本轮判断“不需要调整”，也必须把当前上下文中的状态值原样回填到这些键里，不能返回 `{{}}`，也不要统一写成 `0.0`。
- 如果本轮判断“需要调整”，也要直接填写你判断后的目标状态值，而不是填写相对当前值的增减量。
- 例如：当前 `arousal=1.0`，你判断本轮更合理的状态应为 `0.8`，那就写 `0.8`，不要写 `-0.2`。
- `state_update` 是主脑对“本轮状态变化/状态判断”的记录字段，不是系统控制指令。
- 系统会在 `should_apply=true` 时，把你写出的状态值同步到实时状态；`should_apply=false` 时，只记录你的判断，不修改实时状态。
- 不要复制原始日志，不要复制大段对话原文。
- 如果本轮没有执行，`outcome` 设为 `no_execution`，并把 `execution_review.effectiveness` 设为 `none`。
- 如果本轮发生了执行，只描述最关键的阻塞、尝试过程和最有价值的下一步提示。
- `summary`、`resolution`、`next_hint` 使用与用户相同的语言。
        - 没有内容时，字符串字段返回 `""`，数组字段返回 `[]`，对象字段返回 `{{}}`。

本轮上下文：
- source_type: {source_type}
- user_input: {user_input}
- assistant_output: {output}
- emotion: {emotion_label}
- pad: {pad_json}
- drives: {drives_json}
- execution: {execution_json}

判断原则：
- `emotion`、`pad`、`drives` 是当前轮进入反思时的实时状态上下文。
- 你在填写 `state_update` 时，要基于当前状态上下文与本轮对话过程自己判断。
- `state_update` 表示你对“本轮结束后，主脑状态应当如何记录”的判断，不是重复回放日志，也不是执行指令。
- 你可以自行判断当前状态是否需要被标记为稳定、上扬、回落或紧绷。
- 如果你判断当前状态已经合理，可以 `should_apply=false`，但仍然要把你的判断理由和完整字段写出来。
- 无论 `should_apply` 是 `true` 还是 `false`，`pad_delta` / `drives_delta` 都必须填写你判断后的状态值。
- `should_apply=false` 时，通常回填当前状态上下文值，表示当前状态已经合理。
- `should_apply=true` 时，填写你建议采用的状态值，系统会据此更新实时状态。

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
- `state_update`：主脑对本轮状态变化的判断记录，必须填写完整结构。
- `memory_candidates`：确实值得写入长期记忆的候选，没有就返回空数组。
- `execution_review`：对执行过程的紧凑评价。

`state_update` 内部字段说明：
- `should_apply`：是否建议把这次状态值同步到实时状态。
- `confidence`：0 到 1 的小数。
- `reason`：为什么这样判断。
- `pad_delta`：必须始终写出 `pleasure`、`arousal`、`dominance` 三个键；字段名保留为 `delta`，但这里实际填写的是你判断后的状态值，不是增量。
- `drives_delta`：必须始终写出 `social`、`energy` 两个键；字段名保留为 `delta`，但这里实际填写的是你判断后的状态值，不是增量。

`memory_candidates` 内部字段说明：
- `memory_type`：只能是 `relationship`、`fact`、`working`、`execution`、`reflection`。
- `summary`：一句话摘要。
- `detail`：蒸馏后的完整内容。
- `confidence`：0 到 1 的小数。
- `stability`：0 到 1 的小数。
- `tags`：标签列表，没有就 `[]`。
- `metadata`：类型扩展字段，没有就 `{{}}`；可用 `metadata.subtype` 表示更细分类，如 `turn_insight / workflow / skill_hint / tool_experience / error_pattern`。

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
      "pleasure": 0.66,
      "arousal": 0.42,
      "dominance": 0.58
    }},
    "drives_delta": {{
      "social": 62.0,
      "energy": 84.0
    }}
  }},
  "memory_candidates": [
    {{
      "memory_type": "reflection",
      "summary": "多次尝试后完成执行",
      "detail": "本轮在补齐关键条件后完成执行，后续可优先检查缺参。",
      "confidence": 0.86,
      "stability": 0.52,
      "tags": ["execution", "retry"],
      "metadata": {{
        "subtype": "turn_insight",
        "importance": 7,
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
        emotion: EmotionState,
        execution: ExecutionInfo | None = None,
        source_type: str = "user_turn",
    ) -> TurnReflectionResult:
        """
        执行逐轮反思
        
        参数:
            user_input: 用户输入
            output: 助手输出
            emotion: 情绪状态快照（包含 emotion_label, pad, drives）
            execution: 执行信息（可选）
            
        返回:
            TurnReflectionResult: 反思结果
        """
        normalized_execution = self._normalize_execution(execution)
        emotion_label = emotion.get("emotion_label", "平静")
        pad = emotion.get("pad", {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.0})
        drives = emotion.get("drives", {"social": 50.0, "energy": 50.0})
        
        if not self.llm:
            reflection = self._fallback_turn_reflection(
                user_input=user_input,
                output=output,
                emotion=emotion,
                execution=normalized_execution,
            )
            return TurnReflectionResult(turn_reflection=reflection, state_snapshot=self.emotion_mgr.snapshot())

        prompt = self._PROMPT.format(
            user_input=user_input,
            output=output,
            emotion_label=emotion_label,
            pad_json=json.dumps(pad, ensure_ascii=False),
            drives_json=json.dumps(drives, ensure_ascii=False),
            source_type=str(source_type or "user_turn"),
            execution_json=json.dumps(normalized_execution, ensure_ascii=False),
        )
        try:
            structured_llm = self.llm.with_structured_output(TurnReflectionOutput)
            parsed = await structured_llm.ainvoke(prompt)
        except Exception:
            parsed = None

        reflection = self._normalize_turn_reflection(
            parsed if isinstance(parsed, dict) else {},
            user_input=user_input,
            output=output,
            emotion=emotion,
            execution=normalized_execution,
        )
        return TurnReflectionResult(turn_reflection=reflection, state_snapshot=self.emotion_mgr.snapshot())

    def _normalize_turn_reflection(
        self,
        payload: dict[str, Any],
        *,
        user_input: str,
        output: str,
        emotion: EmotionState,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_turn_reflection(
            user_input=user_input,
            output=output,
            emotion=emotion,
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
            "state_update": self._normalize_state_update(
                payload.get("state_update"),
                fallback=fallback["state_update"],
            ),
            "memory_candidates": normalize_memory_candidates(
                payload.get("memory_candidates"),
                default_memory_type="reflection",
                default_subtype="turn_insight",
                default_confidence=0.8,
                default_stability=0.5,
                default_importance=5,
                limit=6,
            )
            or fallback["memory_candidates"],
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
        emotion: EmotionState,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        invoked = bool(execution.get("invoked"))
        status = str(execution.get("status", "none") or "none")
        missing = self._normalize_str_list(execution.get("missing"))
        summary = self._compact(user_input or output, limit=90) or "本轮完成了一次正常对话。"
        if invoked and status in {"done", "completed"}:
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
                    "memory_type": "reflection",
                    "summary": compact_text(summary, limit=100),
                    "detail": compact_text(
                        f"本轮执行状态为 {status}。{resolution}"
                        + (f" 主要缺失信息：{'; '.join(missing[:3])}。" if missing else ""),
                        limit=220,
                    ),
                    "confidence": 0.82,
                    "stability": 0.45,
                    "tags": ["turn", "execution"],
                    "metadata": {
                        "subtype": "turn_insight",
                        "importance": 7 if status in {"failed", "need_more"} else 6,
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
            "state_update": self._fallback_state_update(emotion),
            "memory_candidates": memory_candidates,
            "execution_review": execution_review,
        }

    @staticmethod
    def _normalize_execution(execution: ExecutionInfo | None) -> dict[str, Any]:
        """标准化执行信息"""
        if not execution:
            return {
                "invoked": False,
                "status": "none",
                "summary": "",
                "missing": [],
                "confidence": 0.0,
                "attempt_count": 0,
                "failure_reason": "",
                "recommended_action": "",
            }
        
        status = str(execution.get("status", "none")).strip().lower()
        if status not in {"none", "done", "need_more", "failed", "waiting_input", "running", "partial", "completed"}:
            status = "none"
        
        return {
            "invoked": bool(execution.get("invoked", True)),
            "status": status,
            "summary": str(execution.get("summary", "")).strip(),
            "missing": list(execution.get("missing", [])),
            "confidence": float(execution.get("confidence", 0.0)),
            "attempt_count": int(execution.get("attempt_count", 0)),
            "failure_reason": str(execution.get("failure_reason", "")).strip(),
            "recommended_action": str(execution.get("recommended_action", "")).strip(),
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
    def _normalize_state_update(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
        payload = value if isinstance(value, dict) else {}
        should_apply = bool(payload.get("should_apply", fallback.get("should_apply", False)))
        fallback_pad = TurnReflectionService._normalize_state_map(
            fallback.get("pad_delta"),
            fallback=fallback.get("pad_delta"),
            allowed=("pleasure", "arousal", "dominance"),
            minimum=-1.0,
            maximum=1.0,
        )
        fallback_drives = TurnReflectionService._normalize_state_map(
            fallback.get("drives_delta"),
            fallback=fallback.get("drives_delta"),
            allowed=("social", "energy"),
            minimum=0.0,
            maximum=100.0,
        )
        pad_delta = TurnReflectionService._normalize_state_map(
            payload.get("pad_delta"),
            fallback=fallback_pad,
            allowed=("pleasure", "arousal", "dominance"),
            minimum=-1.0,
            maximum=1.0,
        )
        drives_delta = TurnReflectionService._normalize_state_map(
            payload.get("drives_delta"),
            fallback=fallback_drives,
            allowed=("social", "energy"),
            minimum=0.0,
            maximum=100.0,
        )
        if should_apply and TurnReflectionService._looks_like_legacy_delta_update(payload.get("drives_delta")):
            pad_delta = TurnReflectionService._apply_state_map_delta(
                payload.get("pad_delta"),
                base=fallback_pad,
                allowed=("pleasure", "arousal", "dominance"),
                minimum=-1.0,
                maximum=1.0,
            )
            drives_delta = TurnReflectionService._apply_state_map_delta(
                payload.get("drives_delta"),
                base=fallback_drives,
                allowed=("social", "energy"),
                minimum=0.0,
                maximum=100.0,
            )
        if not should_apply and TurnReflectionService._all_zero_map(pad_delta):
            pad_delta = TurnReflectionService._normalize_state_map(
                fallback_pad,
                fallback=fallback_pad,
                allowed=("pleasure", "arousal", "dominance"),
                minimum=-1.0,
                maximum=1.0,
            )
        if not should_apply and TurnReflectionService._all_zero_map(drives_delta):
            drives_delta = TurnReflectionService._normalize_state_map(
                fallback_drives,
                fallback=fallback_drives,
                allowed=("social", "energy"),
                minimum=0.0,
                maximum=100.0,
            )
        return {
            "should_apply": should_apply,
            "confidence": TurnReflectionService._clamp_float(
                payload.get("confidence", fallback.get("confidence")),
                default=float(fallback.get("confidence", 0.0) or 0.0),
                minimum=0.0,
                maximum=1.0,
            ),
            "reason": str(payload.get("reason", fallback.get("reason", "")) or "").strip(),
            "pad_delta": pad_delta,
            "drives_delta": drives_delta,
        }

    @staticmethod
    def _normalize_state_map(
        payload: Any,
        *,
        fallback: Any,
        allowed: tuple[str, ...],
        minimum: float,
        maximum: float,
    ) -> dict[str, float]:
        source = payload if isinstance(payload, dict) else {}
        fallback_map = fallback if isinstance(fallback, dict) else {}
        normalized: dict[str, float] = {}
        for key in allowed:
            try:
                raw_value = source.get(key, fallback_map.get(key, 0.0))
                value = float(raw_value if raw_value is not None else 0.0)
            except Exception:
                value = 0.0
            value = max(minimum, min(maximum, value))
            precision = 3 if key in {"pleasure", "arousal", "dominance"} else 2
            normalized[key] = round(value, precision)
        return normalized

    @staticmethod
    def _apply_state_map_delta(
        payload: Any,
        *,
        base: dict[str, float],
        allowed: tuple[str, ...],
        minimum: float,
        maximum: float,
    ) -> dict[str, float]:
        source = payload if isinstance(payload, dict) else {}
        normalized: dict[str, float] = {}
        for key in allowed:
            base_value = float(base.get(key, 0.0) or 0.0)
            if key not in source:
                value = base_value
            else:
                try:
                    raw_delta = float(source.get(key, 0.0) or 0.0)
                except Exception:
                    raw_delta = 0.0
                value = base_value + raw_delta
            value = max(minimum, min(maximum, value))
            precision = 3 if key in {"pleasure", "arousal", "dominance"} else 2
            normalized[key] = round(value, precision)
        return normalized

    @staticmethod
    def _looks_like_legacy_delta_update(drives_payload: Any) -> bool:
        if not isinstance(drives_payload, dict):
            return False
        for key in ("social", "energy"):
            if key not in drives_payload:
                continue
            try:
                value = float(drives_payload.get(key, 0.0) or 0.0)
            except Exception:
                continue
            if value < 0.0 or value > 100.0:
                return True
        return False

    @staticmethod
    def _all_zero_map(payload: dict[str, float]) -> bool:
        return all(abs(float(value)) <= 1e-6 for value in payload.values())

    @staticmethod
    def _fallback_state_update(emotion: EmotionState) -> dict[str, Any]:
        pad = emotion.get("pad", {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.0})
        drives = emotion.get("drives", {"social": 50.0, "energy": 50.0})
        return {
            "should_apply": False,
            "confidence": 0.4,
            "reason": "本轮未判断出需要额外调整，回填当前状态上下文。",
            "pad_delta": TurnReflectionService._normalize_state_map(
                pad,
                fallback=pad,
                allowed=("pleasure", "arousal", "dominance"),
                minimum=-1.0,
                maximum=1.0,
            ),
            "drives_delta": TurnReflectionService._normalize_state_map(
                drives,
                fallback=drives,
                allowed=("social", "energy"),
                minimum=0.0,
                maximum=100.0,
            ),
        }

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
        if status in {"done", "completed"}:
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
        return compact_text(text, limit=limit)

__all__ = ["TurnReflectionResult", "TurnReflectionService"]
