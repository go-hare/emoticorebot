"""Per-turn reflection aligned with the new architecture."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.reflection.candidates import compact_text, normalize_memory_candidates
from emoticorebot.types import EmotionState, ExecutionInfo, TurnReflectionOutput
from emoticorebot.utils.llm_utils import extract_message_text


@dataclass(frozen=True)
class TurnReflectionResult:
    """逐轮反思结果"""
    turn_reflection: TurnReflectionOutput  # 反思输出
    state_snapshot: dict[str, Any] | None = None  # 情绪状态快照


class TurnReflectionUnavailable(RuntimeError):
    """Raised when turn reflection cannot be generated from a real model result."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason or "").strip() or "turn_reflection_unavailable"


class TurnReflectionService:
    """Generate one compact per-turn reflection plus memory candidates."""

    _JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

    _PROMPT = """
你是 `left_brain` 的逐轮反思环节。

请严格按结构化字段填写，不要补充额外说明。

任务：
1. 总结本轮发生了什么。
2. 列出本轮暴露出的主要问题（如果有）。
3. 说明问题最终是如何解决的。
4. 从 `left_brain` 视角评价本轮 `task` 执行情况。
5. 只在确实有长期价值时，产出少量长期记忆候选。
6. 只在本轮存在高置信、可直接落盘的信息时，填写 `user_updates` 与 `soul_updates`。
7. 必须填写 `state_update`，并且 `pad_delta` / `drives_delta` 不能返回空对象。

规则：
- `memory_candidates` 必须简洁，并且足够稳定，能帮助未来轮次。
- `user_updates` / `soul_updates` 的每一项都必须是可直接写入 Markdown 列表的单句结论。
- `user_updates` 聚焦用户本轮明确表达出的稳定事实、偏好、目标、边界或协作习惯。
- `soul_updates` 聚焦左脑本轮需要立即记住的表达方式、风格要求或协作策略修正。
- `state_update` 必须始终填写。
- `pad_delta` 必须始终包含 `pleasure`、`arousal`、`dominance` 三个键。
- `drives_delta` 必须始终包含 `social`、`energy` 两个键。
- 字段名虽然叫 `pad_delta` / `drives_delta`，但在这里必须填写“你判断后的状态值”，不要填写增量、差值或 `+0.1` / `-2.0` 这种微调量。
- 如果本轮判断“不需要调整”，也必须把当前上下文中的状态值原样回填到这些键里，不能返回 `{{}}`，也不要统一写成 `0.0`。
- 如果本轮判断“需要调整”，也要直接填写你判断后的目标状态值，而不是填写相对当前值的增减量。
- 例如：当前 `arousal=1.0`，你判断本轮更合理的状态应为 `0.8`，那就写 `0.8`，不要写 `-0.2`。
- `state_update` 是左脑对“本轮状态变化/状态判断”的记录字段，不是系统控制指令。
- 系统会在 `should_apply=true` 时，把你写出的状态值同步到实时状态；`should_apply=false` 时，只记录你的判断，不修改实时状态。
- 不要复制原始日志，不要复制大段对话原文。
- 如果本轮没有执行，`outcome` 设为 `no_execution`，并把 `execution_review.effectiveness` 设为 `none`。
- 如果本轮发生了执行，只描述最关键的阻塞、尝试过程和最有价值的下一步提示。
- 如果 `task_trace` 里出现了工具报错、重试、路径错误、参数错误，但最终又成功了，`problems` 与 `resolution` 也必须如实体现，不要只写“执行成功”。
- `needs_deep_reflection` 只在你判断“当前这轮虽然完成了浅反思，但还暴露出值得继续做深反思的重复模式或稳定问题”时设为 `true`；普通单轮问题默认 `false`。
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
- task: {task_json}
- task_trace: {task_trace_json}
- metadata: {metadata_json}

判断原则：
- `emotion`、`pad`、`drives` 是当前轮进入反思时的实时状态上下文。
- 你在填写 `state_update` 时，要基于当前状态上下文与本轮对话过程自己判断。
- `state_update` 表示你对“本轮结束后，左脑状态应当如何记录”的判断，不是重复回放日志，也不是执行指令。
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
  "needs_deep_reflection": false,
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
    "effectiveness": "high|medium|low|none",
    "main_failure_reason": "",
    "next_execution_hint": ""
  }}
}}

字段说明：
- `summary`：本轮最核心的总结。
- `problems`：本轮暴露出来的问题列表，没有就返回空数组。
- `resolution`：这些问题最终是如何被解决的。
- `outcome`：只能是 `success`、`partial`、`failed`、`no_execution`。
- `next_hint`：下一轮左脑最值得记住的承接提示。
- `needs_deep_reflection`：是否建议系统在本轮浅反思结束后继续触发一次深反思。
- `user_updates`：本轮新增的用户信息直写候选，没有就返回空数组。
- `soul_updates`：本轮新增的左脑风格修正直写候选，没有就返回空数组。
- `state_update`：左脑对本轮状态变化的判断记录，必须填写完整结构。
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
  "needs_deep_reflection": false,
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
        "resolution": "补齐条件后成功执行",
        "outcome": "success",
        "follow_up": "下次先检查关键参数"
      }}
    }}
  ],
  "execution_review": {{
    "effectiveness": "high",
    "main_failure_reason": "",
    "next_execution_hint": ""
  }}
}}
""".strip()

    def __init__(self, emotion_manager: EmotionStateManager, llm: Any):
        self.emotion_mgr = emotion_manager
        self.llm = llm

    async def run_turn_reflection(
        self,
        *,
        user_input: str,
        output: str,
        emotion: EmotionState,
        execution: ExecutionInfo | None = None,
        source_type: str = "user_turn",
        task: dict[str, Any] | None = None,
        task_trace: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
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
            raise TurnReflectionUnavailable("turn_reflection_llm_unavailable")

        compact_task = {
            key: value
            for key, value in dict(task or {}).items()
            if key in {"task_id", "title", "state", "result", "summary", "error"} and value not in ("", None, [], {})
        }
        compact_trace = []
        for item in list(task_trace or [])[-12:]:
            if not isinstance(item, Mapping):
                continue
            data = item.get("data")
            compact_trace.append(
                {
                    key: value
                    for key, value in {
                        "kind": str(item.get("kind", "") or "").strip(),
                        "message": str(item.get("message", "") or "").strip(),
                        "tool_name": (
                            str(item.get("tool_name", "") or "").strip()
                            or (str(data.get("tool_name", "") or "").strip() if isinstance(data, Mapping) else "")
                        ),
                        "event": str(data.get("event", "") or "").strip() if isinstance(data, Mapping) else "",
                        "source_event": str(data.get("source_event", "") or "").strip()
                        if isinstance(data, Mapping)
                        else "",
                    }.items()
                    if value not in ("", None, [], {})
                }
            )
        compact_metadata = {
            key: value
            for key, value in dict(metadata or {}).items()
            if key in {"recent_turns", "short_term_memory", "long_term_memory", "memory_refs", "tool_usage_summary"}
            and value not in ("", None, [], {})
        }

        prompt = self._PROMPT.format(
            user_input=user_input,
            output=output,
            emotion_label=emotion_label,
            pad_json=json.dumps(pad, ensure_ascii=False),
            drives_json=json.dumps(drives, ensure_ascii=False),
            source_type=str(source_type or "user_turn"),
            execution_json=json.dumps(normalized_execution, ensure_ascii=False),
            task_json=json.dumps(compact_task, ensure_ascii=False),
            task_trace_json=json.dumps(compact_trace, ensure_ascii=False),
            metadata_json=json.dumps(compact_metadata, ensure_ascii=False),
        )

        parsed = await self._invoke_reflection_model(prompt)

        reflection = self._normalize_turn_reflection(
            parsed,
            user_input=user_input,
            output=output,
            emotion=emotion,
            execution=normalized_execution,
            task_trace=list(task_trace or []),
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
        task_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        trace_problems = self._trace_problems(task_trace=list(task_trace or []), execution=execution)
        default_effectiveness = self._default_effectiveness(execution=execution, trace_problems=trace_problems)
        default_failure_reason = trace_problems[0] if trace_problems else str(execution.get("failure_reason", "") or "").strip()
        default_hint = self._default_next_hint(trace_problems)
        normalized = {
            "summary": str(payload.get("summary", "") or "").strip(),
            "problems": self._normalize_str_list(payload.get("problems")) or trace_problems,
            "resolution": str(payload.get("resolution", "") or "").strip(),
            "outcome": self._normalize_outcome(payload.get("outcome"), self._default_outcome(execution)),
            "next_hint": str(payload.get("next_hint", "") or "").strip() or default_hint,
            "needs_deep_reflection": self._normalize_bool(payload.get("needs_deep_reflection"), default=False),
            "user_updates": self._normalize_str_list(payload.get("user_updates")),
            "soul_updates": self._normalize_str_list(payload.get("soul_updates")),
            "state_update": self._normalize_state_update(
                payload.get("state_update"),
                fallback=self._fallback_state_update(emotion),
            ),
            "memory_candidates": normalize_memory_candidates(
                payload.get("memory_candidates"),
                default_memory_type="reflection",
                default_subtype="turn_insight",
                default_confidence=0.8,
                default_stability=0.5,
                default_importance=5,
                limit=6,
            ),
            "execution_review": self._normalize_execution_review(
                payload.get("execution_review"),
                fallback={
                    "effectiveness": default_effectiveness,
                    "main_failure_reason": default_failure_reason,
                    "next_execution_hint": default_hint,
                },
            ),
        }
        if not any(
            (
                normalized["summary"],
                normalized["problems"],
                normalized["resolution"],
                normalized["user_updates"],
                normalized["soul_updates"],
                normalized["memory_candidates"],
            )
        ):
            raise TurnReflectionUnavailable("turn_reflection_empty_payload")
        return normalized

    async def _invoke_reflection_model(self, prompt: str) -> dict[str, Any]:
        structured_error: Exception | None = None
        if hasattr(self.llm, "with_structured_output"):
            try:
                structured_llm = self.llm.with_structured_output(TurnReflectionOutput)
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
                raise TurnReflectionUnavailable("turn_reflection_llm_missing_invoke")
            payload = self._parse_json_payload(extract_message_text(response))
            if payload:
                return payload
        except TurnReflectionUnavailable:
            raise
        except Exception as exc:
            if structured_error is None:
                structured_error = exc

        raise TurnReflectionUnavailable(
            "turn_reflection_generation_failed"
            + (f": {type(structured_error).__name__}" if structured_error is not None else "")
        )

    @staticmethod
    def _normalize_execution(execution: ExecutionInfo | None) -> dict[str, Any]:
        """标准化执行信息"""
        if not execution:
            return {
                "invoked": False,
                "status": "none",
                "summary": "",
                "failure_reason": "",
            }
        
        status = str(execution.get("status", "none")).strip().lower()
        if status not in {"none", "done", "failed", "running", "partial", "completed"}:
            status = "none"
        
        return {
            "invoked": bool(execution.get("invoked", True)),
            "status": status,
            "summary": str(execution.get("summary", "")).strip(),
            "failure_reason": str(execution.get("failure_reason", "")).strip(),
        }

    @staticmethod
    def _normalize_execution_review(payload: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
        review = payload if isinstance(payload, dict) else {}
        return {
            "effectiveness": TurnReflectionService._normalize_effectiveness(
                review.get("effectiveness"),
                default=str(fallback.get("effectiveness", "none") or "none"),
            ),
            "main_failure_reason": str(review.get("main_failure_reason", fallback.get("main_failure_reason", "")) or "").strip(),
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
    def _normalize_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off"}:
            return False
        return default

    @staticmethod
    def _default_effectiveness(*, execution: dict[str, Any], trace_problems: list[str]) -> str:
        if not execution.get("invoked"):
            return "none"
        status = str(execution.get("status", "none") or "none")
        if trace_problems and status in {"done", "completed"}:
            return "medium"
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

    @staticmethod
    def _default_outcome(execution: dict[str, Any]) -> str:
        if not execution.get("invoked"):
            return "no_execution"
        status = str(execution.get("status", "none") or "none")
        if status in {"done", "completed"}:
            return "success"
        if status == "failed":
            return "failed"
        return "partial"

    @staticmethod
    def _default_next_hint(trace_problems: list[str]) -> str:
        if not trace_problems:
            return ""
        return "下次先检查工作目录、工具参数和验证路径，再继续执行。"

    @staticmethod
    def _trace_problems(*, task_trace: list[dict[str, Any]], execution: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        failure_reason = str(execution.get("failure_reason", "") or "").strip()
        if failure_reason:
            problems.append(failure_reason)
        for item in task_trace:
            if not isinstance(item, Mapping):
                continue
            message = str(item.get("message", "") or "").strip()
            lower = message.lower()
            if not message:
                continue
            if "error" not in lower and "invalid" not in lower and "no such file" not in lower and "failed" not in lower:
                continue
            compact = compact_text(message, limit=120)
            if compact and compact not in problems:
                problems.append(compact)
        return problems[:4]

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

__all__ = ["TurnReflectionResult", "TurnReflectionService"]
