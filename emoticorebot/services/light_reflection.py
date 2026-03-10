"""Per-turn light reflection for fast user/soul/state updates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.utils.llm_utils import extract_message_text


@dataclass(frozen=True)
class LightReflectionResult:
    light_insight: dict[str, Any]
    applied_user: bool = False
    applied_soul: bool = False
    applied_state: bool = False
    state_snapshot: dict[str, Any] | None = None


class LightReflectionService:
    """Generate a real-time reflection and apply only safe high-confidence updates."""

    _PROMPT = """
You are the main brain's real-time light reflection loop for a companion AI.

Task:
1. Summarize this turn in one short sentence.
2. Decide whether there is an immediate relationship shift or context update.
3. Review this turn's execution/tool-use path from the main brain perspective.
3. Decide whether this turn contains high-confidence direct updates for:
   - USER.md: explicit stable user facts or preferences
   - SOUL.md: explicit user-requested style/personality adjustment for the AI
   - current_state.md: small immediate PAD / drive deltas for the current turn

Rules:
- Only update USER.md when the user explicitly states a fact, preference, role, or stable style request.
- Only update SOUL.md when the user explicitly asks the AI to speak/act in a certain way.
- For current state, output small deltas only. Do not replace the whole state.
- If there was execution this turn, summarize whether it was effective, what blocked it, and the best next execution hint.
- If there was no execution this turn, set execution_review.effectiveness to "none" and keep other execution_review fields empty.
- Preserve the full markdown structure if you rewrite USER.md or SOUL.md.
- If confidence is low, do not apply updates.
- Return JSON only.

Current USER.md:
{current_user}

Current SOUL.md:
{current_soul}

Current current_state.md:
{current_state}

Turn context:
- user_input: {user_input}
- assistant_output: {output}
- current_emotion: {emotion_label}
- pad: {pad_json}
- drives: {drives_json}
- execution_snapshot: {execution_json}
- executor_trace:
{executor_trace_block}

Required JSON schema:
{{
  "summary": "short sentence",
  "relation_shift": "trust_up|trust_down|stable",
  "context_update": "short text",
  "next_hint": "short text",
  "execution_review": {{
    "summary": "short text",
    "effectiveness": "high|medium|low|none",
    "failure_reason": "short label or empty",
    "missing_inputs": ["..."],
    "next_execution_hint": "short text"
  }},
  "user_profile_facts": ["..."],
  "soul_preferences": ["..."],
  "update_user": {{
    "should_write": false,
    "confidence": 0.0,
    "reason": "",
    "content": null
  }},
  "update_soul": {{
    "should_write": false,
    "confidence": 0.0,
    "reason": "",
    "content": null
  }},
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
  }}
}}
""".strip()

    def __init__(
        self,
        workspace: Path,
        emotion_manager: EmotionStateManager,
        llm: Any,
    ):
        self.workspace = workspace
        self.emotion_mgr = emotion_manager
        self.llm = llm
        self.user_threshold = 0.8
        self.soul_threshold = 0.8
        self.state_threshold = 0.65

    async def reflect_turn(
        self,
        *,
        user_input: str,
        output: str,
        emotion_label: str,
        pad: dict[str, float],
        drives: dict[str, float],
        execution: dict[str, Any] | None = None,
        executor_trace: list[dict[str, Any]] | None = None,
    ) -> LightReflectionResult:
        if not user_input.strip() or not output.strip():
            return LightReflectionResult(
                light_insight=self._fallback_light_insight(
                    execution=execution,
                    executor_trace=executor_trace,
                )
            )

        execution_snapshot = self._normalize_execution_payload(execution)
        trace_block = self._build_execution_trace_block(executor_trace)

        if not self.llm:
            return LightReflectionResult(
                light_insight=self._fallback_light_insight(
                    execution=execution_snapshot,
                    executor_trace=executor_trace,
                )
            )

        current_user = self._ensure_md_file("USER.md")
        current_soul = self._ensure_md_file("SOUL.md")
        current_state = self.emotion_mgr.read_md()

        prompt = self._PROMPT.format(
            current_user=current_user,
            current_soul=current_soul,
            current_state=current_state,
            user_input=user_input,
            output=output,
            emotion_label=emotion_label,
            pad_json=json.dumps(pad, ensure_ascii=False),
            drives_json=json.dumps(drives, ensure_ascii=False),
            execution_json=json.dumps(execution_snapshot, ensure_ascii=False),
            executor_trace_block=trace_block or "(none)",
        )

        try:
            resp = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            raw = extract_message_text(resp)
            parsed = self._extract_json(raw)
            if not parsed:
                logger.warning("LightReflectionService: no JSON found in model output")
                return LightReflectionResult(
                    light_insight=self._fallback_light_insight(
                        execution=execution_snapshot,
                        executor_trace=executor_trace,
                    )
                )

            applied_user = False
            applied_soul = False
            applied_state = False
            state_snapshot: dict[str, Any] | None = None

            user_update = parsed.get("update_user") or {}
            if self._should_apply(user_update, threshold=self.user_threshold):
                content = str(user_update.get("content", "") or "").strip()
                if content and self._validate_user_update(current_user, content):
                    applied_user = self._safe_write_text(self.workspace / "USER.md", content)

            soul_update = parsed.get("update_soul") or {}
            if self._should_apply(soul_update, threshold=self.soul_threshold):
                content = str(soul_update.get("content", "") or "").strip()
                if content and self._validate_soul_update(current_soul, content):
                    applied_soul = self._safe_write_text(self.workspace / "SOUL.md", content)

            state_update = parsed.get("state_update") or {}
            if self._should_apply(state_update, threshold=self.state_threshold):
                pad_delta = self._normalize_delta_map(
                    state_update.get("pad_delta"),
                    allowed=("pleasure", "arousal", "dominance"),
                    max_abs=0.3,
                )
                drives_delta = self._normalize_delta_map(
                    state_update.get("drives_delta"),
                    allowed=("social", "energy"),
                    max_abs=20.0,
                )
                if pad_delta or drives_delta:
                    state_snapshot = self.emotion_mgr.apply_reflection_state_update(
                        pad_delta=pad_delta,
                        drive_delta=drives_delta,
                    )
                    applied_state = True

            light_insight = self._build_light_insight(
                parsed=parsed,
                applied_user=applied_user,
                applied_soul=applied_soul,
                applied_state=applied_state,
                state_snapshot=state_snapshot,
                execution=execution_snapshot,
                executor_trace=executor_trace,
            )
            return LightReflectionResult(
                light_insight=light_insight,
                applied_user=applied_user,
                applied_soul=applied_soul,
                applied_state=applied_state,
                state_snapshot=state_snapshot,
            )
        except Exception as exc:
            logger.warning("LightReflectionService failed: {}", exc)
            return LightReflectionResult(
                light_insight=self._fallback_light_insight(
                    execution=execution_snapshot,
                    executor_trace=executor_trace,
                )
            )

    def _ensure_md_file(self, filename: str) -> str:
        target = self.workspace / filename
        if target.exists():
            return target.read_text(encoding="utf-8")
        template = self._load_template(filename)
        if template:
            target.write_text(template, encoding="utf-8")
            return template
        return ""

    @staticmethod
    def _load_template(filename: str) -> str:
        try:
            return (files("emoticorebot") / "templates" / filename).read_text(encoding="utf-8")
        except Exception:
            return ""

    @staticmethod
    def _should_apply(payload: Any, *, threshold: float) -> bool:
        if not isinstance(payload, dict):
            return False
        if not bool(payload.get("should_write", payload.get("should_apply", False))):
            return False
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except Exception:
            return False
        return confidence >= threshold

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
        for key in allowed:
            if key not in payload:
                continue
            try:
                value = float(payload.get(key, 0.0) or 0.0)
            except Exception:
                continue
            value = max(-max_abs, min(max_abs, value))
            if abs(value) > 1e-6:
                normalized[key] = round(value, 3 if max_abs <= 1.0 else 2)
        return normalized

    def _build_light_insight(
        self,
        *,
        parsed: dict[str, Any],
        applied_user: bool,
        applied_soul: bool,
        applied_state: bool,
        state_snapshot: dict[str, Any] | None,
        execution: dict[str, Any] | None,
        executor_trace: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        state_update = parsed.get("state_update") or {}
        execution_review = self._normalize_execution_review(
            parsed.get("execution_review"),
            execution=execution,
            executor_trace=executor_trace,
        )
        direct_updates = {
            "user_profile": self._normalize_string_list(parsed.get("user_profile_facts")),
            "soul_preferences": self._normalize_string_list(parsed.get("soul_preferences")),
            "current_state_updates": {
                "pad": self._normalize_delta_map(
                    state_update.get("pad_delta"),
                    allowed=("pleasure", "arousal", "dominance"),
                    max_abs=0.3,
                ) or None,
                "drives": self._normalize_delta_map(
                    state_update.get("drives_delta"),
                    allowed=("social", "energy"),
                    max_abs=20.0,
                ) or None,
            },
            "applied": {
                "user": applied_user,
                "soul": applied_soul,
                "state": applied_state,
            },
            "applied_state_snapshot": state_snapshot or {},
        }
        next_hint = str(parsed.get("next_hint", "") or "").strip()
        return {
            "summary": str(parsed.get("summary", "") or "").strip(),
            "relation_shift": str(parsed.get("relation_shift", "stable") or "stable").strip(),
            "context_update": str(parsed.get("context_update", "") or "").strip(),
            "next_hint": next_hint,
            "execution_review": execution_review,
            "direct_updates": direct_updates,
        }

    @classmethod
    def _fallback_light_insight(
        cls,
        *,
        execution: dict[str, Any] | None = None,
        executor_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        light_insight = cls._default_light_insight()
        execution_review = cls._infer_execution_review(
            execution=execution,
            executor_trace=executor_trace,
        )
        light_insight["execution_review"] = execution_review
        return light_insight

    @classmethod
    def _default_light_insight(cls) -> dict[str, Any]:
        return {
            "summary": "",
            "relation_shift": "stable",
            "context_update": "",
            "next_hint": "",
            "execution_review": cls._default_execution_review(),
            "direct_updates": {
                "user_profile": [],
                "soul_preferences": [],
                "current_state_updates": {
                    "pad": None,
                    "drives": None,
                },
                "applied": {
                    "user": False,
                    "soul": False,
                    "state": False,
                },
                "applied_state_snapshot": {},
            },
        }

    @staticmethod
    def _default_execution_review() -> dict[str, Any]:
        return {
            "summary": "",
            "effectiveness": "none",
            "failure_reason": "",
            "missing_inputs": [],
            "next_execution_hint": "",
        }

    @classmethod
    def _normalize_execution_review(
        cls,
        payload: Any,
        *,
        execution: dict[str, Any] | None,
        executor_trace: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        inferred = cls._infer_execution_review(
            execution=execution,
            executor_trace=executor_trace,
        )
        if not cls._execution_invoked(execution=execution, executor_trace=executor_trace):
            return inferred
        if not isinstance(payload, dict):
            return inferred

        effectiveness = cls._normalize_effectiveness(
            payload.get("effectiveness"),
            default=str(inferred.get("effectiveness", "none") or "none"),
        )
        failure_reason = str(payload.get("failure_reason", "") or "").strip()
        if not failure_reason and effectiveness != "high":
            failure_reason = str(inferred.get("failure_reason", "") or "").strip()

        missing_inputs = cls._normalize_string_list(payload.get("missing_inputs"))
        if not missing_inputs:
            missing_inputs = list(inferred.get("missing_inputs", []) or [])

        next_execution_hint = str(payload.get("next_execution_hint", "") or "").strip()
        if not next_execution_hint:
            next_execution_hint = str(inferred.get("next_execution_hint", "") or "").strip()

        summary = str(payload.get("summary", "") or "").strip()
        if not summary:
            summary = str(inferred.get("summary", "") or "").strip()

        return {
            "summary": summary,
            "effectiveness": effectiveness,
            "failure_reason": failure_reason,
            "missing_inputs": missing_inputs,
            "next_execution_hint": next_execution_hint,
        }

    @classmethod
    def _infer_execution_review(
        cls,
        *,
        execution: dict[str, Any] | None,
        executor_trace: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        normalized_execution = cls._normalize_execution_payload(execution)
        if not cls._execution_invoked(execution=normalized_execution, executor_trace=executor_trace):
            return cls._default_execution_review()

        pending_review = normalized_execution.get("pending_review") if isinstance(normalized_execution.get("pending_review"), dict) else {}
        action_requests = pending_review.get("action_requests") if isinstance(pending_review.get("action_requests"), list) else []
        missing_inputs = cls._normalize_string_list(normalized_execution.get("missing"))
        control_state = str(normalized_execution.get("control_state", "idle") or "idle").strip()
        status = str(normalized_execution.get("status", "none") or "none").strip()
        tool_names = cls._extract_tool_names(executor_trace)
        failure_reason = cls._infer_execution_failure_reason(
            execution=normalized_execution,
            executor_trace=executor_trace,
        )

        if not normalized_execution.get("invoked", False):
            effectiveness = "none"
        elif status == "done" and not action_requests and not missing_inputs and not failure_reason:
            effectiveness = "high"
        elif status == "failed":
            effectiveness = "low"
        elif control_state == "paused" or status == "need_more" or action_requests or missing_inputs:
            effectiveness = "medium"
        else:
            effectiveness = "medium"

        summary = ""
        if action_requests:
            summary = "执行已暂停，等待审批后继续。"
        elif missing_inputs:
            summary = "执行已暂停，缺少继续所需的信息。"
        elif status == "failed":
            summary = "执行路径未成功收敛，需要调整做法。"
        elif status == "done":
            if tool_names:
                summary = f"执行已完成，主要通过 {', '.join(tool_names[:3])} 获得结果。"
            else:
                summary = "执行已完成，并形成了可用结果。"
        elif control_state == "running":
            summary = "执行已启动，但本轮还没有稳定结论。"
        else:
            summary = "本轮进行了执行尝试。"

        next_execution_hint = cls._build_execution_next_hint(
            failure_reason=failure_reason,
            missing_inputs=missing_inputs,
            action_requests=action_requests,
            control_state=control_state,
            status=status,
        )

        return {
            "summary": summary,
            "effectiveness": effectiveness,
            "failure_reason": failure_reason,
            "missing_inputs": missing_inputs,
            "next_execution_hint": next_execution_hint,
        }

    @staticmethod
    def _normalize_execution_payload(execution: Any) -> dict[str, Any]:
        payload = execution if isinstance(execution, dict) else {}
        missing = LightReflectionService._normalize_string_list(payload.get("missing"))
        pending_review = payload.get("pending_review") if isinstance(payload.get("pending_review"), dict) else {}
        control_state = str(payload.get("control_state", "idle") or "idle").strip().lower()
        status = str(payload.get("status", "none") or "none").strip().lower()
        invoked = bool(payload.get("invoked", False)) or any(
            [
                str(payload.get("thread_id", "") or "").strip(),
                str(payload.get("run_id", "") or "").strip(),
                str(payload.get("summary", "") or "").strip(),
                missing,
                pending_review,
                control_state not in {"", "idle"},
                status not in {"", "none"},
            ]
        )
        return {
            "invoked": invoked,
            "control_state": control_state if control_state else ("completed" if invoked else "idle"),
            "status": status if status else ("done" if invoked else "none"),
            "summary": LightReflectionService._compact(str(payload.get("summary", "") or "").strip(), limit=180),
            "missing": missing,
            "pending_review": dict(pending_review),
            "recommended_action": str(payload.get("recommended_action", "") or "").strip(),
        }

    @classmethod
    def _execution_invoked(
        cls,
        *,
        execution: dict[str, Any] | None,
        executor_trace: list[dict[str, Any]] | None,
    ) -> bool:
        normalized_execution = cls._normalize_execution_payload(execution)
        return bool(normalized_execution.get("invoked")) or bool(cls._normalize_trace_entries(executor_trace))

    @classmethod
    def _build_execution_trace_block(cls, executor_trace: list[dict[str, Any]] | None) -> str:
        entries = cls._normalize_trace_entries(executor_trace)
        if not entries:
            return ""

        lines: list[str] = []
        for entry in entries[-8:]:
            role = str(entry.get("role", "") or "").strip() or str(entry.get("phase", "") or "").strip() or "trace"
            tool_name = str(entry.get("tool_name", "") or entry.get("name", "") or "").strip()
            content = cls._compact(cls._trace_entry_content(entry), limit=180)
            tool_calls = entry.get("tool_calls") if isinstance(entry.get("tool_calls"), list) else []
            tool_call_names = [
                str(item.get("name", "") or "").strip()
                for item in tool_calls
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            ]
            parts = [role]
            if tool_name:
                parts.append(f"tool={tool_name}")
            if tool_call_names:
                parts.append(f"tool_calls={', '.join(tool_call_names[:4])}")
            if content:
                parts.append(f"content={content}")
            lines.append("- " + " | ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _normalize_trace_entries(executor_trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not isinstance(executor_trace, list):
            return []
        return [dict(item) for item in executor_trace if isinstance(item, dict)]

    @staticmethod
    def _trace_entry_content(entry: dict[str, Any]) -> str:
        content = entry.get("content")
        if isinstance(content, list):
            return " ".join(str(item).strip() for item in content if str(item).strip())
        if content is not None:
            return str(content).strip()
        if entry.get("event"):
            return str(entry.get("event", "") or "").strip()
        return ""

    @classmethod
    def _extract_tool_names(cls, executor_trace: list[dict[str, Any]] | None) -> list[str]:
        names: list[str] = []
        for entry in cls._normalize_trace_entries(executor_trace):
            tool_name = str(entry.get("tool_name", "") or entry.get("name", "") or "").strip()
            if tool_name and tool_name not in names:
                names.append(tool_name)
            tool_calls = entry.get("tool_calls") if isinstance(entry.get("tool_calls"), list) else []
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                call_name = str(item.get("name", "") or "").strip()
                if call_name and call_name not in names:
                    names.append(call_name)
        return names[:6]

    @classmethod
    def _infer_execution_failure_reason(
        cls,
        *,
        execution: dict[str, Any],
        executor_trace: list[dict[str, Any]] | None,
    ) -> str:
        pending_review = execution.get("pending_review") if isinstance(execution.get("pending_review"), dict) else {}
        action_requests = pending_review.get("action_requests") if isinstance(pending_review.get("action_requests"), list) else []
        if action_requests:
            return "awaiting_approval"
        if cls._normalize_string_list(execution.get("missing")):
            return "missing_information"

        texts = [str(execution.get("summary", "") or "").strip()]
        for entry in cls._normalize_trace_entries(executor_trace)[-6:]:
            trace_text = cls._trace_entry_content(entry)
            if trace_text:
                texts.append(trace_text)
        haystack = " ".join(texts).lower()

        if "invalid parameters" in haystack or "missing required" in haystack:
            return "invalid_parameters"
        if "not found" in haystack:
            return "not_found"
        if "permission" in haystack or "access is denied" in haystack:
            return "permission_denied"
        if "timeout" in haystack:
            return "timeout"
        if "network" in haystack or "dns" in haystack or "connection" in haystack:
            return "network_error"
        if str(execution.get("status", "") or "").strip() == "failed":
            return "execution_failed"
        return ""

    @staticmethod
    def _build_execution_next_hint(
        *,
        failure_reason: str,
        missing_inputs: list[str],
        action_requests: list[Any],
        control_state: str,
        status: str,
    ) -> str:
        if action_requests:
            return "先完成审批决定，再恢复当前执行。"
        if missing_inputs:
            return f"补充这些信息后可继续：{', '.join(missing_inputs[:4])}"
        if failure_reason == "invalid_parameters":
            return "把参数缩到最小有效集合后再试一次。"
        if failure_reason == "not_found":
            return "先核对路径、标识符或 URL，再决定是否重试。"
        if failure_reason == "permission_denied":
            return "先切换到允许的路径或取得授权，再恢复执行。"
        if failure_reason == "timeout":
            return "缩小执行范围或延长超时后再重试。"
        if failure_reason == "network_error":
            return "先确认连通性，或改用更窄的检索范围。"
        if failure_reason in {"execution_failed", "unknown_failure"}:
            return "需要换一条更稳的执行路径。"
        if control_state == "paused" or status == "need_more":
            return "等待主脑决定是继续补证据还是先向用户追问。"
        return ""

    @staticmethod
    def _normalize_effectiveness(value: Any, *, default: str = "none") -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"high", "medium", "low", "none"}:
            return normalized
        return default

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:8]

    @staticmethod
    def _extract_markers(text: str) -> tuple[list[str], bool]:
        markers: list[str] = []
        has_header_comment = False
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(">"):
                has_header_comment = True
                continue
            if stripped.startswith("#"):
                markers.append(stripped)
        return markers, has_header_comment

    def _validate_soul_update(self, current: str, updated: str) -> bool:
        updated_clean = updated.strip()
        if len(updated_clean) < 20:
            return False
        current_markers, current_has_header_comment = self._extract_markers(current)
        updated_markers, updated_has_header_comment = self._extract_markers(updated)
        if current_has_header_comment and not updated_has_header_comment:
            return False
        if current_markers and not set(current_markers).issubset(set(updated_markers)):
            return False
        return True

    @staticmethod
    def _contains_emotion_cognition_section(text: str) -> bool:
        lowered = text.lower()
        return ("\u60c5\u611f\u8ba4\u77e5" in text) or ("emotion cognition" in lowered)

    def _validate_user_update(self, current: str, updated: str) -> bool:
        updated_clean = updated.strip()
        if len(updated_clean) < 10:
            return False
        current_markers, _ = self._extract_markers(current)
        updated_markers, _ = self._extract_markers(updated)
        if current_markers and not set(current_markers).issubset(set(updated_markers)):
            return False
        if self._contains_emotion_cognition_section(current) and (
            not self._contains_emotion_cognition_section(updated)
        ):
            return False
        return True

    @staticmethod
    def _safe_write_text(target: Path, content: str) -> bool:
        backup = target.with_suffix(target.suffix + ".bak")
        temp = target.with_suffix(target.suffix + ".tmp")
        previous = target.read_text(encoding="utf-8") if target.exists() else None
        try:
            if previous is not None:
                backup.write_text(previous, encoding="utf-8")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
            return True
        except Exception as exc:
            logger.warning("LightReflectionService safe write failed for {}: {}", target.name, exc)
            try:
                if previous is not None:
                    target.write_text(previous, encoding="utf-8")
            except Exception:
                pass
            return False
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except Exception:
                    pass

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(text.strip())
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _compact(text: str, *, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."


__all__ = ["LightReflectionResult", "LightReflectionService"]
