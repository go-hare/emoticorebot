"""Periodic tool reflection and consolidation into knowledge memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.utils.llm_utils import extract_message_text


@dataclass(frozen=True)
class ToolDeepReflectionResult:
    memory_updates: list[str] = field(default_factory=list)
    insight_count: int = 0


class ToolDeepReflectionService:
    """Summarize recent tool reflections into stable knowledge memory."""

    _PROMPT = """
You are the periodic tool reflection process for an executor system.

Your job:
1. Read recent tool_light_reflection records.
2. Summarize stable successful patterns and stable failure patterns.
3. Suggest tool choices worth reusing.
4. Identify candidates that should become reusable skills.

Rules:
- Focus on repeated patterns, not one-off noise.
- Keep every list concise and reusable.
- Return JSON only.

Recent tool reflections:
{reflection_block}

Required JSON schema:
{{
  "reliable_patterns": ["..."],
  "failure_patterns": ["..."],
  "recommended_tool_choices": ["..."],
  "skill_candidates": ["..."]
}}
""".strip()

    def __init__(self, workspace: Path, llm: Any):
        self.workspace = workspace
        self.llm = llm
        self.memory_file = workspace / "memory" / "knowledge_memory.jsonl"
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)

    async def run_cycle(self, limit: int = 24) -> ToolDeepReflectionResult:
        reflections = self._load_recent_light_reflections(limit=limit)
        if not self.llm or len(reflections) < 3:
            return ToolDeepReflectionResult()

        prompt = self._PROMPT.format(reflection_block=self._build_reflection_block(reflections))
        try:
            resp = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            raw = extract_message_text(resp)
            parsed = self._extract_json(raw)
            if not parsed:
                logger.warning("ToolDeepReflectionService: no JSON found in model output")
                return ToolDeepReflectionResult()
            added = self._append_deep_reflection(parsed, source_entries=reflections)
            if not added:
                return ToolDeepReflectionResult()
            return ToolDeepReflectionResult(memory_updates=[f"knowledge_memory:{added}"], insight_count=added)
        except Exception as exc:
            logger.warning("ToolDeepReflectionService failed: {}", exc)
            return ToolDeepReflectionResult()

    def _load_recent_light_reflections(self, *, limit: int) -> list[dict[str, Any]]:
        if not self.memory_file.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.memory_file.open("r", encoding="utf-8") as file_obj:
                for line in file_obj:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type", "") or "").strip() != "tool_light_reflection":
                        continue
                    rows.append(item)
        except Exception:
            return []
        return rows[-limit:]

    @staticmethod
    def _build_reflection_block(reflections: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for item in reflections:
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            tool_name = str(meta.get("tool_name", "") or "").strip() or "unknown"
            effectiveness = str(item.get("effectiveness", "") or "").strip() or "unknown"
            summary = str(item.get("summary", "") or "").strip()
            failure_reason = str(item.get("failure_reason", "") or "").strip()
            missing_inputs = item.get("missing_inputs") if isinstance(item.get("missing_inputs"), list) else []
            next_hint = str(item.get("next_hint", "") or "").strip()
            parts = [f"tool={tool_name}", f"effectiveness={effectiveness}"]
            if summary:
                parts.append(f"summary={summary}")
            if failure_reason:
                parts.append(f"failure_reason={failure_reason}")
            if missing_inputs:
                parts.append(f"missing_inputs={missing_inputs}")
            if next_hint:
                parts.append(f"next_hint={next_hint}")
            lines.append("; ".join(parts))
        return "\n".join(lines)

    def _append_deep_reflection(self, payload: dict[str, Any], *, source_entries: list[dict[str, Any]]) -> int:
        reliable_patterns = self._normalize_list(payload.get("reliable_patterns"))
        failure_patterns = self._normalize_list(payload.get("failure_patterns"))
        recommended_tool_choices = self._normalize_list(payload.get("recommended_tool_choices"))
        skill_candidates = self._normalize_list(payload.get("skill_candidates"))
        if not any((reliable_patterns, failure_patterns, recommended_tool_choices, skill_candidates)):
            return 0

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "tool_deep_reflection",
            "reliable_patterns": reliable_patterns,
            "failure_patterns": failure_patterns,
            "recommended_tool_choices": recommended_tool_choices,
            "skill_candidates": skill_candidates,
        }

        signature = self._build_signature(entry)
        if signature in self._existing_signatures():
            return 0

        try:
            with self.memory_file.open("a", encoding="utf-8") as file_obj:
                file_obj.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return 1
        except Exception as exc:
            logger.warning("ToolDeepReflectionService write failed: {}", exc)
            return 0

    def _existing_signatures(self) -> set[str]:
        if not self.memory_file.exists():
            return set()
        signatures: set[str] = set()
        try:
            with self.memory_file.open("r", encoding="utf-8") as file_obj:
                for line in file_obj:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type", "") or "").strip() != "tool_deep_reflection":
                        continue
                    signatures.add(self._build_signature(item))
        except Exception:
            return set()
        return signatures

    @staticmethod
    def _build_signature(item: dict[str, Any]) -> str:
        payload = {
            "reliable_patterns": ToolDeepReflectionService._normalize_list(item.get("reliable_patterns")),
            "failure_patterns": ToolDeepReflectionService._normalize_list(item.get("failure_patterns")),
            "recommended_tool_choices": ToolDeepReflectionService._normalize_list(item.get("recommended_tool_choices")),
            "skill_candidates": ToolDeepReflectionService._normalize_list(item.get("skill_candidates")),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = " ".join(str(item or "").split()).strip()
            if text and text not in result:
                result.append(text)
        return result[:12]

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
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


__all__ = ["ToolDeepReflectionService", "ToolDeepReflectionResult"]
