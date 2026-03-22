"""Workspace skill generation from crystallized reflection records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from emoticorebot.state.schemas import LongTermRecord, MemoryCandidate, MemoryPatch
from emoticorebot.utils.helpers import ensure_dir


class SkillStore:
    """Generate workspace skills from crystallized long-term records."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = ensure_dir(workspace / "skills")

    def write_from_memory_patch(self, patch: MemoryPatch, reason: str) -> list[Path]:
        paths: list[Path] = []
        for record in patch.long_term_append:
            path = self.write_from_record(record, reason)
            if path is not None:
                paths.append(path)
        return paths

    def write_from_record(self, record: LongTermRecord, reason: str) -> Path | None:
        normalized = LongTermRecord.model_validate(record.model_dump())
        candidates = self.skill_candidates(normalized.memory_candidates)
        if not candidates:
            return None
        slug = self.skill_slug(normalized, candidates)
        skill_dir = ensure_dir(self.root / slug)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(self.render_skill(slug, normalized, candidates, reason), encoding="utf-8")
        return skill_path

    def skill_candidates(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        allowed = {"working", "execution", "reflection"}
        return [item for item in candidates if item.memory_type in allowed]

    def skill_slug(self, record: LongTermRecord, candidates: list[MemoryCandidate]) -> str:
        primary = candidates[0]
        raw = primary.memory_id or record.record_id or primary.summary or record.summary or "reflection-skill"
        cleaned = raw.replace("_", "-").replace(" ", "-").lower()
        cleaned = re.sub(r"[^a-z0-9-]+", "-", cleaned)
        cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
        if cleaned.startswith("mem-"):
            cleaned = cleaned[4:]
        if cleaned.startswith("memory-"):
            cleaned = cleaned[7:]
        if cleaned.startswith("record-"):
            cleaned = cleaned[7:]
        return cleaned or "reflection-skill"

    def render_skill(
        self,
        slug: str,
        record: LongTermRecord,
        candidates: list[MemoryCandidate],
        reason: str,
    ) -> str:
        title = self.skill_title(slug, candidates)
        description = self.skill_description(slug, record, candidates)
        metadata = {
            "emoticorebot": {
                "source": "reflection",
                "reason": reason,
                "record_id": record.record_id,
                "session_id": record.session_id,
                "thread_id": record.thread_id,
            }
        }
        lines = [
            "---",
            f"name: {slug}",
            f'description: "{description}"',
            f"metadata: {json.dumps(metadata, ensure_ascii=False)}",
            "---",
            "",
            f"# {title}",
            "",
            "Auto-generated from crystallized reflection. Keep this skill concise and update it when the same pattern becomes clearer.",
            "",
            "## Core Lesson",
            record.summary or candidates[0].summary,
            "",
            "## When To Use",
        ]
        for line in self.when_to_use_lines(candidates):
            lines.append(f"- {line}")
        lines.extend(["", "## Guidance"])
        for candidate in candidates:
            lines.extend(self.render_candidate(candidate))
        if record.user_updates:
            lines.extend(["", "## User Context"])
            for item in record.user_updates:
                if item.strip():
                    lines.append(f"- {item.strip()}")
        if record.soul_updates:
            lines.extend(["", "## Style Context"])
            for item in record.soul_updates:
                if item.strip():
                    lines.append(f"- {item.strip()}")
        return "\n".join(lines).rstrip() + "\n"

    def skill_title(self, slug: str, candidates: list[MemoryCandidate]) -> str:
        primary = candidates[0].summary.strip() or slug.replace("-", " ")
        return primary

    def skill_description(self, slug: str, record: LongTermRecord, candidates: list[MemoryCandidate]) -> str:
        primary = candidates[0]
        tags = [tag.strip() for tag in primary.tags if tag.strip()]
        tag_text = ", ".join(tags[:4]) if tags else primary.memory_type
        base = primary.summary.strip() or record.summary.strip() or slug
        return f"Auto-generated reflection skill. Use when handling {tag_text} work, especially when {base}."

    def when_to_use_lines(self, candidates: list[MemoryCandidate]) -> list[str]:
        lines: list[str] = []
        for candidate in candidates:
            summary = candidate.summary.strip()
            if summary and summary not in lines:
                lines.append(summary)
            for tag in candidate.tags[:4]:
                tag_text = str(tag or "").strip()
                if tag_text:
                    item = f"Tasks involving {tag_text}"
                    if item not in lines:
                        lines.append(item)
        lines.append("Repeated failures point to the same workflow gap or missing check")
        return lines

    def render_candidate(self, candidate: MemoryCandidate) -> list[str]:
        lines = [
            "",
            f"### {candidate.summary.strip() or candidate.memory_id or candidate.memory_type}",
            "",
            f"- Type: `{candidate.memory_type}`",
        ]
        detail = candidate.detail.strip()
        if detail:
            lines.append(f"- Detail: {detail}")
        tags = [tag.strip() for tag in candidate.tags if tag.strip()]
        if tags:
            lines.append(f"- Tags: {', '.join(tags)}")
        checkpoints = self.checkpoints_from_candidate(candidate)
        if checkpoints:
            lines.append("- Checkpoints:")
            for item in checkpoints:
                lines.append(f"  - {item}")
        return lines

    def checkpoints_from_candidate(self, candidate: MemoryCandidate) -> list[str]:
        metadata = dict(candidate.metadata or {})
        checkpoints: list[str] = []
        error_types = metadata.get("error_types")
        if isinstance(error_types, list):
            for item in error_types:
                text = str(item or "").strip().replace("_", " ")
                if text:
                    checkpoints.append(f"Watch for {text}")
        lesson = str(metadata.get("lesson_learned", "") or "").strip().replace("_", " ")
        if lesson:
            checkpoints.append(f"Apply lesson: {lesson}")
        if "working_dir" in candidate.detail:
            checkpoints.append("Confirm working directory before file or shell operations")
        if "路径" in candidate.detail or "path" in candidate.detail.lower():
            checkpoints.append("Confirm the path is valid relative to the workspace")
        if "参数" in candidate.detail or "param" in candidate.detail.lower():
            checkpoints.append("Confirm required parameters are present before execution")
        deduped: list[str] = []
        for item in checkpoints:
            if item not in deduped:
                deduped.append(item)
        return deduped
