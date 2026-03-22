"""Prompt assembly for the front service."""

from __future__ import annotations

from pathlib import Path

from emoticorebot.state.schemas import MemoryView


class FrontPromptBuilder:
    """Build prompts for fast user-facing replies."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def build_user_prompt(self, *, user_text: str, memory: MemoryView) -> str:
        sections = [
            "## 用户输入",
            user_text.strip(),
        ]
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        current_state = str(memory.current_state or "").strip()
        if soul_anchor:
            sections.extend(["", "## 灵魂锚点", soul_anchor])
        if user_anchor:
            sections.extend(["", "## 用户画像", user_anchor])
        if current_state:
            sections.extend(["", "## 当前状态", current_state])
        recent_dialogue = list(memory.raw_layer.get("recent_dialogue", []) or [])
        if recent_dialogue:
            lines = []
            for row in recent_dialogue[-6:]:
                role = str(row.get("role", "") or "").strip() or "unknown"
                content = str(row.get("content", "") or "").strip()
                if content:
                    lines.append(f"{role}: {content}")
            if lines:
                sections.extend(["", "## 最近对话", "\n".join(lines)])
        return "\n".join(part for part in sections if part is not None)

    def build_followup_prompt(self, *, intent_text: str, memory: MemoryView) -> str:
        sections = [
            "## Core 意图",
            intent_text.strip(),
        ]
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        if soul_anchor:
            sections.extend(["", "## 灵魂锚点", soul_anchor])
        if user_anchor:
            sections.extend(["", "## 用户画像", user_anchor])
        return "\n".join(part for part in sections if part is not None)
