"""Workspace skill storage and retrieval."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from emoticorebot.utils.helpers import ensure_dir


class SkillStore:
    """Owns workspace skills and generated crystallized skills."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = ensure_dir(workspace / "skills")
        self.generated_root = ensure_dir(self.root / "generated")

    def search(self, query: str, limit: int = 4) -> list[dict[str, str]]:
        text = str(query or "").strip().lower()
        rows: list[tuple[float, dict[str, str]]] = []
        for path in sorted(self.root.rglob("SKILL.md")):
            record = self.read_skill(path)
            if not record:
                continue
            score = self.score_skill(record, text)
            if score <= 0:
                continue
            rows.append((score, record))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in rows[:limit]]

    def render_context(self, query: str, limit: int = 4) -> str:
        skills = self.search(query, limit=limit)
        if not skills:
            return ""
        lines: list[str] = []
        for skill in skills:
            lines.extend(
                [
                    f"### {skill['title']}",
                    f"- path: {skill['path']}",
                ]
            )
            description = skill.get("description", "")
            excerpt = skill.get("excerpt", "")
            if description:
                lines.append(f"- description: {description}")
            if excerpt:
                lines.append(excerpt)
            lines.append("")
        return "\n".join(lines).strip()

    def write_generated_skill(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        safe_slug = self.normalize_slug(slug or title or "generated-skill")
        skill_dir = ensure_dir(self.generated_root / safe_slug)
        path = skill_dir / "SKILL.md"
        front_matter = {
            "name": safe_slug,
            "description": description or title or safe_slug,
            "metadata": metadata or {},
        }
        body = [
            "---",
            yaml.safe_dump(front_matter, allow_unicode=True, sort_keys=False).strip(),
            "---",
            "",
            f"# {title or safe_slug}",
            "",
            content.strip(),
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        return path

    def read_skill(self, path: Path) -> dict[str, str] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            return None
        front_matter, body = self.split_front_matter(raw)
        title = self.extract_title(body) or path.parent.name
        description = str(front_matter.get("description", "") or "").strip()
        return {
            "path": str(path),
            "title": title,
            "description": description,
            "excerpt": self.make_excerpt(body),
            "content": body.strip(),
        }

    def split_front_matter(self, raw: str) -> tuple[dict[str, Any], str]:
        text = str(raw or "")
        if not text.startswith("---\n"):
            return {}, text
        parts = text.split("\n---\n", 1)
        if len(parts) != 2:
            return {}, text
        front_matter_text = parts[0][4:]
        body = parts[1]
        try:
            payload = yaml.safe_load(front_matter_text) or {}
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}, body

    def extract_title(self, body: str) -> str:
        for line in body.splitlines():
            text = line.strip()
            if text.startswith("# "):
                return text[2:].strip()
        return ""

    def make_excerpt(self, body: str, max_lines: int = 6) -> str:
        rows: list[str] = []
        for line in body.splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            rows.append(text)
            if len(rows) >= max_lines:
                break
        return "\n".join(rows)

    def score_skill(self, record: dict[str, str], query: str) -> float:
        if not query:
            return 0.1
        tokens = self.tokenize(query)
        haystack = " ".join(
            [
                record.get("title", ""),
                record.get("description", ""),
                record.get("excerpt", ""),
                record.get("content", ""),
            ]
        ).lower()
        skill_tokens = self.tokenize(haystack)
        if not tokens or not skill_tokens:
            return 0.0
        return len(tokens & skill_tokens) / max(1, len(tokens))

    def tokenize(self, text: str) -> set[str]:
        return {token for token in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if token}

    def normalize_slug(self, value: str) -> str:
        text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
        text = re.sub(r"[^a-z0-9-]+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text or "generated-skill"

