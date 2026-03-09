"""Periodic deep reflection and long-term memory consolidation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger

from emoticorebot.utils.llm_utils import extract_message_text


@dataclass(frozen=True)
class DeepReflectionResult:
    persona_delta: str | None = None
    user_insight: str | None = None
    memory_updates: list[str] = field(default_factory=list)
    insight_count: int = 0


class DeepReflectionService:
    """Summarize recent cognitive events into durable memory and optional file updates."""

    _PROMPT = """
You are the main brain's periodic deep reflection process for a companion AI.

Your job:
1. Read recent cognitive events and their light insights.
2. Distill stable long-term memories, not one-off noise.
3. Optionally update USER.md and SOUL.md only when the pattern is stable enough.

Rules:
- Write self memories only for stable brain-side style or response patterns.
- Write relation memories only for stable user preference, trust, familiarity, or relationship phase signals.
- Write insight memories for higher-level observations that should guide future reflection.
- Do not turn a single momentary mood into a long-term memory.
- Only rewrite USER.md or SOUL.md if confidence is high and the whole markdown should be replaced.
- Keep existing markdown structure if you rewrite either file.
- Return JSON only.

Current SOUL.md:
{current_soul}

Current USER.md:
{current_user}

Recent cognitive events:
{event_block}

Required JSON schema:
{{
  "summary": "short paragraph",
  "self_memories": [
    {{"memory": "stable self pattern", "confidence": 0.0, "evidence": ["evt_x"]}}
  ],
  "relation_memories": [
    {{"memory": "stable user or relation pattern", "confidence": 0.0, "evidence": ["evt_x"]}}
  ],
  "insight_memories": [
    {{"memory": "higher-level insight", "confidence": 0.0, "evidence": ["evt_x"]}}
  ],
  "soul_update": {{
    "should_write": false,
    "confidence": 0.0,
    "content": null
  }},
  "user_update": {{
    "should_write": false,
    "confidence": 0.0,
    "content": null
  }}
}}
""".strip()

    def __init__(self, workspace: Path, llm: Any):
        self.workspace = workspace
        self.llm = llm
        self.memory_dir = workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.self_memory_file = self.memory_dir / "self_memory.jsonl"
        self.relation_memory_file = self.memory_dir / "relation_memory.jsonl"
        self.insight_memory_file = self.memory_dir / "insight_memory.jsonl"
        self.memory_threshold = 0.72
        self.doc_threshold = 0.82

    async def run_cycle(self, events: list[dict[str, Any]]) -> DeepReflectionResult:
        if not self.llm or not events:
            return DeepReflectionResult()

        current_soul = self._ensure_md_file("SOUL.md")
        current_user = self._ensure_md_file("USER.md")
        prompt = self._PROMPT.format(
            current_soul=current_soul,
            current_user=current_user,
            event_block=self._build_event_block(events),
        )

        try:
            resp = await self.llm.ainvoke([{"role": "user", "content": prompt}])
            raw = extract_message_text(resp)
            parsed = self._extract_json(raw)
            if not parsed:
                logger.warning("DeepReflectionService: no JSON found in model output")
                return DeepReflectionResult()

            memory_updates: list[str] = []
            insight_count = 0

            self_added = self._append_memories(
                self.self_memory_file,
                parsed.get("self_memories"),
                memory_type="self_memory",
                source_events=events,
                summary=str(parsed.get("summary", "") or ""),
            )
            if self_added:
                memory_updates.append(f"self_memory:{self_added}")
                insight_count += self_added

            relation_added = self._append_memories(
                self.relation_memory_file,
                parsed.get("relation_memories"),
                memory_type="relation_memory",
                source_events=events,
                summary=str(parsed.get("summary", "") or ""),
            )
            if relation_added:
                memory_updates.append(f"relation_memory:{relation_added}")
                insight_count += relation_added

            insight_added = self._append_memories(
                self.insight_memory_file,
                parsed.get("insight_memories"),
                memory_type="insight_memory",
                source_events=events,
                summary=str(parsed.get("summary", "") or ""),
            )
            if insight_added:
                memory_updates.append(f"insight_memory:{insight_added}")
                insight_count += insight_added

            persona_delta = self._maybe_update_doc(
                target=self.workspace / "SOUL.md",
                current=current_soul,
                payload=parsed.get("soul_update"),
                validator=self._validate_soul_update,
                label="SOUL.md updated",
            )
            user_insight = self._maybe_update_doc(
                target=self.workspace / "USER.md",
                current=current_user,
                payload=parsed.get("user_update"),
                validator=self._validate_user_update,
                label="USER.md updated",
            )

            return DeepReflectionResult(
                persona_delta=persona_delta,
                user_insight=user_insight,
                memory_updates=memory_updates,
                insight_count=insight_count,
            )
        except Exception as exc:
            logger.warning("DeepReflectionService failed: {}", exc)
            return DeepReflectionResult()

    def _build_event_block(self, events: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for event in events:
            event_id = str(event.get("id", "") or "")
            actor = str(event.get("actor", "") or "event")
            timestamp = str(event.get("timestamp", "") or "")[:16]
            content = self._compact_text(str(event.get("content", "") or ""), limit=120)
            relation_state = ((event.get("state") or {}).get("relation_state") or {}) if isinstance(event.get("state"), dict) else {}
            self_state = ((event.get("state") or {}).get("self_state") or {}) if isinstance(event.get("state"), dict) else {}
            light_insight = event.get("light_insight") or {}
            mood = str((self_state or {}).get("mood", "") or "stable")
            relation = str((relation_state or {}).get("stage", "") or (relation_state or {}).get("signal", "") or "stable")
            insight_summary = self._compact_text(str((light_insight or {}).get("summary", "") or ""), limit=80)
            relation_shift = str((light_insight or {}).get("relation_shift", "") or "stable")
            context_update = self._compact_text(str((light_insight or {}).get("context_update", "") or ""), limit=80)
            lines.append(
                f"- {event_id} [{timestamp}] [{actor}] mood={mood} relation={relation} shift={relation_shift} "
                f"content={content} insight={insight_summary} context={context_update}"
            )
        return "\n".join(lines)

    def _append_memories(
        self,
        target: Path,
        payload: Any,
        *,
        memory_type: str,
        source_events: list[dict[str, Any]],
        summary: str,
    ) -> int:
        if not isinstance(payload, list):
            return 0
        existing = self._read_existing_memory_texts(target)
        added = 0
        with target.open("a", encoding="utf-8") as file_obj:
            for item in payload:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("memory", "") or "").strip()
                if not text or text in existing:
                    continue
                try:
                    confidence = float(item.get("confidence", 0.0) or 0.0)
                except Exception:
                    confidence = 0.0
                if confidence < self.memory_threshold:
                    continue
                evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
                entry = {
                    "timestamp": datetime.now().isoformat(),
                    "type": memory_type,
                    "memory": text,
                    "confidence": round(confidence, 2),
                    "summary": summary.strip(),
                    "evidence": [str(ev).strip() for ev in evidence if str(ev).strip()],
                    "source_event_ids": [str(event.get("id", "") or "") for event in source_events if str(event.get("id", "") or "").strip()],
                }
                file_obj.write(json.dumps(entry, ensure_ascii=False) + "\n")
                existing.add(text)
                added += 1
        return added

    @staticmethod
    def _read_existing_memory_texts(target: Path) -> set[str]:
        if not target.exists():
            return set()
        seen: set[str] = set()
        try:
            with target.open("r", encoding="utf-8") as file_obj:
                for line in file_obj:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        text = str(parsed.get("memory", "") or "").strip()
                        if text:
                            seen.add(text)
        except Exception:
            return set()
        return seen

    def _maybe_update_doc(
        self,
        *,
        target: Path,
        current: str,
        payload: Any,
        validator: Any,
        label: str,
    ) -> str | None:
        if not isinstance(payload, dict):
            return None
        if not bool(payload.get("should_write", False)):
            return None
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except Exception:
            return None
        if confidence < self.doc_threshold:
            return None
        content = str(payload.get("content", "") or "").strip()
        if not content or not validator(current, content):
            return None
        if self._safe_write_text(target, content):
            return label
        return None

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
            logger.warning("DeepReflectionService safe write failed for {}: {}", target.name, exc)
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
    def _compact_text(text: str, *, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."


__all__ = ["DeepReflectionResult", "DeepReflectionService"]
