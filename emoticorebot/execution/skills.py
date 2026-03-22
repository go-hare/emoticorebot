"""Skill loading for the execution layer."""

from __future__ import annotations

from pathlib import Path


class SkillLibrary:
    """List workspace and built-in skills for prompts."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.workspace_root = workspace / "skills"
        self.builtin_root = Path(__file__).resolve().parents[1] / "skills"

    def list_entries(self) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        for root, source in ((self.workspace_root, "workspace"), (self.builtin_root, "builtin")):
            if not root.exists():
                continue
            for skill_dir in sorted(root.iterdir()):
                skill_file = skill_dir / "SKILL.md"
                if not skill_dir.is_dir() or not skill_file.exists():
                    continue
                if any(item["name"] == skill_dir.name for item in entries):
                    continue
                entries.append({"name": skill_dir.name, "path": str(skill_file), "source": source})
        return entries

    def build_summary(self) -> str:
        lines = []
        for entry in self.list_entries():
            lines.append(f"- {entry['name']} ({entry['source']}): {entry['path']}")
        return "\n".join(lines)
