"""Skill crystallization from repeated long-term memory patterns."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from emoticorebot.executor.skills import BUILTIN_SKILLS_DIR

from .store import MemoryStore


@dataclass(frozen=True)
class SkillMaterializationResult:
    skill_names: list[str] = field(default_factory=list)
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0


class SkillMaterializer:
    """Turn repeated procedural hints into lightweight `SKILL.md` files."""

    _SKILL_MEMORY_SUBTYPES = {"skill_hint", "skill"}
    _MIN_CLUSTER_SIMILARITY = 0.40

    def __init__(self, workspace: Path, memory_store: MemoryStore, *, min_support: int = 2):
        self.workspace = workspace
        self.memory_store = memory_store
        self.min_support = max(1, int(min_support or 2))
        self.workspace_skills_dir = self.workspace / "skills"

    def materialize_from_memory(self) -> SkillMaterializationResult:
        hints = self._load_active_skill_hints()
        if not hints:
            return SkillMaterializationResult()

        grouped = self._group_hints(hints)
        created_count = 0
        updated_count = 0
        skipped_count = 0
        skill_names: list[str] = []

        for slug, records in grouped.items():
            if len(records) < self.min_support:
                continue

            if self._builtin_skill_exists(slug):
                skipped_count += 1
                continue

            skill_dir = self.workspace_skills_dir / slug
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists() and not self._is_generated_skill(skill_file):
                skipped_count += 1
                continue

            content = self._render_skill(slug=slug, records=records)
            skill_dir.mkdir(parents=True, exist_ok=True)
            previous = skill_file.read_text(encoding="utf-8") if skill_file.exists() else ""
            if previous == content:
                skill_names.append(slug)
                continue

            skill_file.write_text(content, encoding="utf-8")
            skill_names.append(slug)
            if previous:
                updated_count += 1
            else:
                created_count += 1

        return SkillMaterializationResult(
            skill_names=skill_names,
            created_count=created_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
        )

    def _load_active_skill_hints(self) -> list[dict[str, Any]]:
        records = self.memory_store.read_all()
        return [
            record
            for record in records
            if str(((record.get("metadata") or {}).get("subtype", "") or "")) in self._SKILL_MEMORY_SUBTYPES
            and str(record.get("status", "active") or "active") == "active"
        ]

    def _group_hints(self, hints: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        clusters: list[dict[str, Any]] = []
        for record in hints:
            slug, priority = self._slug_candidate(record)
            if not slug:
                continue
            tokens = self._signature_tokens(record)

            matched_cluster: dict[str, Any] | None = None
            for cluster in clusters:
                if slug == cluster["slug"]:
                    matched_cluster = cluster
                    break
                similarities = [
                    self._token_overlap(tokens, signature)
                    for signature in list(cluster.get("signatures", []) or [])
                ]
                if similarities and max(similarities) >= self._MIN_CLUSTER_SIMILARITY:
                    matched_cluster = cluster
                    break

            if matched_cluster is None:
                cluster = {
                    "slug": slug,
                    "slug_priority": priority,
                    "records": [record],
                    "signatures": [tokens],
                }
                clusters.append(cluster)
                grouped[slug] = cluster["records"]
                continue

            matched_cluster["records"].append(record)
            matched_cluster.setdefault("signatures", []).append(tokens)
            if priority > int(matched_cluster.get("slug_priority", -1)):
                old_slug = str(matched_cluster.get("slug", "") or "").strip()
                new_slug = slug
                records_for_slug = grouped.pop(old_slug, None)
                if records_for_slug is not None:
                    grouped[new_slug] = records_for_slug
                matched_cluster["slug"] = new_slug
                matched_cluster["slug_priority"] = priority
        return grouped

    @classmethod
    def _slug_candidate(cls, record: dict[str, Any]) -> tuple[str, int]:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        skill_name = cls._normalize_slug(str(metadata.get("skill_name", "") or ""))
        if skill_name:
            return skill_name, 2
        skill_id = cls._normalize_slug(str(metadata.get("skill_id", "") or ""))
        if skill_id:
            return skill_id, 1
        return cls._normalize_slug(str(record.get("summary", "") or "")), 0

    @staticmethod
    def _normalize_slug(value: str) -> str:
        text = str(value or "").strip().lower()
        text = text.replace("skill_", "")
        text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text

    @staticmethod
    def _tokenize_text(value: str) -> set[str]:
        text = str(value or "").strip().lower()
        if not text:
            return set()
        tokens: list[str] = []
        buffer: list[str] = []
        for char in text:
            if char.isascii() and char.isalnum():
                buffer.append(char)
                continue
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
            if "\u4e00" <= char <= "\u9fff":
                tokens.append(char)
        if buffer:
            tokens.append("".join(buffer))
        return {token for token in tokens if token}

    @classmethod
    def _signature_tokens(cls, record: dict[str, Any]) -> set[str]:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        combined = " ".join(
            [
                str(record.get("summary", "") or ""),
                str(record.get("detail", "") or ""),
                str(metadata.get("skill_name", "") or ""),
                str(metadata.get("trigger", "") or ""),
                str(metadata.get("hint", "") or ""),
            ]
        )
        return cls._tokenize_text(combined)

    @staticmethod
    def _token_overlap(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        shared = len(left & right)
        baseline = min(len(left), len(right))
        if baseline <= 0:
            return 0.0
        return shared / baseline

    @staticmethod
    def _compact(text: str, *, limit: int = 160) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _dedupe_strings(values: list[str]) -> list[str]:
        items: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _is_generated_skill(skill_file: Path) -> bool:
        try:
            content = skill_file.read_text(encoding="utf-8")
        except Exception:
            return False
        return '"generated":true' in content.replace(" ", "")

    @staticmethod
    def _title_from_slug(slug: str) -> str:
        title = " ".join(part.capitalize() for part in slug.split("-") if part)
        return title or "自动生成技能"

    @staticmethod
    def _builtin_skill_exists(slug: str) -> bool:
        return (BUILTIN_SKILLS_DIR / slug / "SKILL.md").exists()

    def _render_skill(self, *, slug: str, records: list[dict[str, Any]]) -> str:
        payloads = [record.get("metadata") if isinstance(record.get("metadata"), dict) else {} for record in records]
        summaries = self._dedupe_strings([str(record.get("summary", "") or "") for record in records])
        contents = self._dedupe_strings([str(record.get("detail", "") or "") for record in records])
        triggers = self._dedupe_strings([str(payload.get("trigger", "") or "") for payload in payloads])
        hints = self._dedupe_strings([str(payload.get("hint", "") or "") for payload in payloads])
        tools = self._dedupe_strings(
            [
                str(tool).strip()
                for payload in payloads
                for tool in list(payload.get("applies_to_tools", []) or [])
            ]
        )
        memory_ids = [
            str(record.get("memory_id", "") or "")
            for record in records
            if str(record.get("memory_id", "") or "")
        ]
        skill_name = next(
            (
                str(payload.get("skill_name", "") or "").strip()
                for payload in payloads
                if str(payload.get("skill_name", "") or "").strip()
            ),
            slug,
        )
        description = self._compact(
            summaries[0] if summaries else contents[0] if contents else f"自动生成技能 `{slug}`",
            limit=140,
        )
        metadata = json.dumps(
            {
                "emoticorebot": {
                    "generated": True,
                    "source": "memory.skill_hint",
                    "skill_id": f"skill_{slug.replace('-', '_')}",
                    "support": len(records),
                    "memory_ids": memory_ids[:12],
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        lines = [
            "---",
            f"name: {slug}",
            f"description: {description}",
            f"metadata: {metadata}",
            "---",
            "",
            f"# {self._title_from_slug(slug)}",
            "",
            "该技能由重复出现的 `skill_hint` 记忆自动沉淀生成。",
            "当触发条件匹配时，优先采用这一套执行方式，并尽量在一次 task 运行内完成收敛。",
            "",
            "## 何时使用",
            "",
        ]

        if triggers:
            lines.extend(f"- {trigger}" for trigger in triggers[:5])
        else:
            lines.append(f"- 当任务与 `{skill_name}` 对应模式相似时")

        lines.extend(["", "## 使用提示", ""])
        guidance = self._dedupe_strings([*hints, *contents, *summaries])
        if guidance:
            lines.extend(f"- {self._compact(item, limit=220)}" for item in guidance[:6])
        else:
            lines.append("- 先明确最终目标，再在单次执行链路内收敛到最终结果。")

        lines.extend(
            [
                "",
                "## 执行流程",
                "",
                "1. 先读取 `brain` 传入的 `goal`、`request`、`constraints` 与 `success_criteria`。",
                "2. 优先复用大脑传入的执行经验、工具经验和其他 `skill_hint`，不要自己检索长期 `memory`。",
                "3. 尽量在一次执行内收敛；如果前置条件不满足，就直接返回 `missing` 或明确失败原因。",
                "4. 最终按 task 协议返回结构化结果，交由 `brain` 做对外表达。",
                "",
                "## 边界",
                "",
                "- 不直接面向用户。",
                "- 不直接检索或写入长期 `memory`。",
                "- 不更新 `SOUL.md`、`USER.md` 或其他技能。",
            ]
        )

        if tools:
            lines.extend(["", "## 工具适配", ""])
            lines.append("- 推荐工具：" + ", ".join(f"`{tool}`" for tool in tools[:8]))

        lines.extend(["", "## 来源", ""])
        lines.append(f"- 来源记忆：{', '.join(memory_ids[:12])}" if memory_ids else "- 来源记忆：无")
        lines.append(f"- 支持次数：{len(records)}")
        return "\n".join(lines).rstrip() + "\n"


__all__ = ["SkillMaterializationResult", "SkillMaterializer"]
