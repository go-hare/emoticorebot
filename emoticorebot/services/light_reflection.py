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
3. Decide whether this turn contains high-confidence direct updates for:
   - USER.md: explicit stable user facts or preferences
   - SOUL.md: explicit user-requested style/personality adjustment for the AI
   - current_state.md: small immediate PAD / drive deltas for the current turn

Rules:
- Only update USER.md when the user explicitly states a fact, preference, role, or stable style request.
- Only update SOUL.md when the user explicitly asks the AI to speak/act in a certain way.
- For current state, output small deltas only. Do not replace the whole state.
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

Required JSON schema:
{{
  "summary": "short sentence",
  "relation_shift": "trust_up|trust_down|stable",
  "context_update": "short text",
  "next_hint": "short text",
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
    ) -> LightReflectionResult:
        if not self.llm or not user_input.strip() or not output.strip():
            return LightReflectionResult(light_insight=self._default_light_insight())

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
        )

        try:
            resp = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            raw = extract_message_text(resp)
            parsed = self._extract_json(raw)
            if not parsed:
                logger.warning("LightReflectionService: no JSON found in model output")
                return LightReflectionResult(light_insight=self._default_light_insight())

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
            return LightReflectionResult(light_insight=self._default_light_insight())

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
    ) -> dict[str, Any]:
        state_update = parsed.get("state_update") or {}
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
        return {
            "summary": str(parsed.get("summary", "") or "").strip(),
            "relation_shift": str(parsed.get("relation_shift", "stable") or "stable").strip(),
            "context_update": str(parsed.get("context_update", "") or "").strip(),
            "next_hint": str(parsed.get("next_hint", "") or "").strip(),
            "direct_updates": direct_updates,
        }

    @staticmethod
    def _default_light_insight() -> dict[str, Any]:
        return {
            "summary": "",
            "relation_shift": "stable",
            "context_update": "",
            "next_hint": "",
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


__all__ = ["LightReflectionResult", "LightReflectionService"]
