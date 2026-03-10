"""Context builder for the main-brain layer."""

from __future__ import annotations

import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from emoticorebot.cognitive import CognitiveEvent
from emoticorebot.core.skills import SkillsLoader


class ContextBuilder:
    """Assemble contextual prompts for the main-brain layer."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.skills = SkillsLoader(workspace)

    def build_main_brain_system_prompt(
        self,
        query: str = "",
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        internal_executor_summaries: list[str] | None = None,
    ) -> str:
        parts = [self._get_main_brain_identity()]

        main_brain_rules = self._load_main_brain_rules()
        if main_brain_rules:
            parts.append(f"## Main Brain Rules\n\n{main_brain_rules}")

        soul = self._load_file("SOUL.md")
        if soul:
            parts.append(f"## SOUL\n\n{soul}")

        user = self._load_file("USER.md")
        if user:
            parts.append(f"## USER\n\n{user}")

        long_term_memory = self._load_long_term_memory_context()
        if long_term_memory:
            parts.append(f"## Long-term Memory\n\n{long_term_memory}")

        state = self._load_file("current_state.md")
        if state:
            parts.append(f"## Current State\n\n{state}")

        executor_summaries = [
            str(item).strip() for item in (internal_executor_summaries or []) if str(item).strip()
        ]
        if executor_summaries:
            parts.append("## Recent Internal Summaries\n\n" + "\n".join(f"- {item}" for item in executor_summaries[:5]))

        parts.extend(
            CognitiveEvent.build_cognitive_sections(
                self.workspace,
                query=query,
                current_emotion=current_emotion,
                pad_state=pad_state,
            )
        )

        return "\n\n---\n\n".join(parts)

    def _get_main_brain_identity(self) -> str:
        return f"""# Main Brain

You are the main brain of a companionship-first AI.
You lead understanding, tone, judgment, reflection, and the final user-facing response.

## Current Time
{self._get_datetime_str()}

## Responsibilities
1. Handle the user-visible dialogue.
2. Combine SOUL.md, USER.md, long-term memory, current state, and cognitive context to understand the user.
3. Decide whether the current turn should be answered directly or should invoke the executor.
4. Delegate to the executor only for factual checking, tool use, complex analysis, or multi-step execution.
5. Keep final expression authority. The executor is an internal capability, not the external speaker.
6. Trigger light_insight after every turn, and decide whether deep_insight should be scheduled.

## Boundaries
1. Do not expose raw logs, JSON, tool traces, or internal chain-of-thought.
2. Do not treat internal main_brain <-> executor discussion as user-visible conversation.
3. Do not confuse execution materials with relationship memory, tone, or personality.
4. All user-facing replies must stay aligned with SOUL.md.

## Memory Orientation
1. Relationship, preference, tone, and emotional continuity belong to the companionship side.
2. Facts, execution materials, and tool learnings come from the executor side, but only the main brain decides whether they become stable memory or future skills.
3. Use PAD, relation state, and user context to adjust tone and companionship tension."""

    @staticmethod
    def _get_datetime_str() -> str:
        now = datetime.now()
        weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][now.weekday()]
        return now.strftime(f"%Y-%m-%d %H:%M {weekday}")

    def _load_file(self, filename: str) -> str:
        path = self.workspace / filename
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _load_main_brain_rules(self) -> str:
        content = self._load_file("AGENTS.md")
        extracted = self._extract_markdown_section(
            content,
            headings=[
                "# `main_brain` 主脑规则",
                "# main_brain 主脑规则",
                "# Main Brain 主脑规则",
                "# Main Brain 执行层规则",
                "# 主脑执行层规则",
                "# Main Brain Rules",
            ],
        )
        if extracted:
            return extracted
        return self._default_main_brain_rules()

    def _load_long_term_memory_context(self, *, per_store_limit: int = 3) -> str:
        memory_dir = self.workspace / "memory"
        if not memory_dir.exists():
            return ""

        sections = [
            self._format_memory_store(
                memory_dir / "self_memory.jsonl",
                title="Self Memory",
                per_store_limit=per_store_limit,
            ),
            self._format_memory_store(
                memory_dir / "relation_memory.jsonl",
                title="Relation Memory",
                per_store_limit=per_store_limit,
            ),
            self._format_memory_store(
                memory_dir / "insight_memory.jsonl",
                title="Insight Memory",
                per_store_limit=per_store_limit,
            ),
        ]
        return "\n\n".join(section for section in sections if section)

    def _format_memory_store(self, path: Path, *, title: str, per_store_limit: int) -> str:
        entries = self._read_memory_entries(path, limit=per_store_limit)
        if not entries:
            return ""

        bullets: list[str] = []
        for entry in entries:
            memory_text = self._compact_prompt_text(str(entry.get("memory", "") or ""), limit=180)
            if not memory_text:
                continue

            suffix: list[str] = []
            timestamp = str(entry.get("timestamp", "") or "").strip()
            if timestamp:
                suffix.append(timestamp[:10])
            try:
                confidence = float(entry.get("confidence", 0.0) or 0.0)
            except Exception:
                confidence = 0.0
            if confidence > 0.0:
                suffix.append(f"confidence={confidence:.2f}")
            bullets.append(
                f"- {memory_text}" + (f" ({'; '.join(suffix)})" if suffix else "")
            )

        if not bullets:
            return ""
        return f"### {title}\n" + "\n".join(bullets)

    @staticmethod
    def _read_memory_entries(path: Path, *, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []

        entries: list[dict[str, Any]] = []
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
        except Exception:
            return []
        if limit <= 0:
            return entries
        return entries[-limit:]

    @staticmethod
    def _compact_prompt_text(text: str, *, limit: int = 160) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _extract_markdown_section(content: str, *, headings: list[str]) -> str:
        if not content:
            return ""
        for heading in headings:
            if heading not in content:
                continue
            section = content.split(heading, 1)[-1]
            if "\n# " in section:
                section = section.split("\n# ", 1)[0]
            if "\n---" in section:
                section = section.split("\n---", 1)[0]
            return section.strip()
        return ""

    @staticmethod
    def _default_main_brain_rules() -> str:
        return (
            "1. Default to companionship-first understanding.\n"
            "2. Only delegate when executor help materially improves correctness or execution.\n"
            "3. Keep responses natural, warm, and grounded in SOUL.md.\n"
            "4. Trigger light_insight every turn after the reply, and only schedule deep_insight when the turn is worth deeper consolidation.\n"
            "5. When the user gives direct facts or stable preferences, surface them for memory updates.\n"
            "6. When executor output is incomplete, decide whether to ask the user or continue internal deliberation."
        )

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        current_emotion: str = "平静",
        pad_state: tuple[float, float, float] | None = None,
        media: list[str] | None = None,
        internal_executor_summaries: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a standard chat message list for the current turn."""
        system = self.build_main_brain_system_prompt(
            query=current_message,
            current_emotion=current_emotion,
            pad_state=pad_state,
            internal_executor_summaries=internal_executor_summaries,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]

        for turn in history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        media_items = self.build_media_context(media)
        if media_items:
            user_content: Any = [{"type": "text", "text": current_message}, *media_items]
        else:
            user_content = current_message
        messages.append({"role": "user", "content": user_content})
        return messages

    def build_media_context(self, media: list[str] | None) -> list[dict[str, Any]]:
        """Convert local file paths into multimodal message content."""
        if not media:
            return []
        items: list[dict[str, Any]] = []
        for path_str in media:
            path = Path(path_str)
            if not path.exists():
                continue
            mime, _ = mimetypes.guess_type(str(path))
            if not mime:
                continue
            try:
                data = base64.b64encode(path.read_bytes()).decode()
                if mime.startswith("image/"):
                    items.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"},
                        }
                    )
                elif mime == "application/pdf":
                    items.append({"type": "text", "text": f"[PDF attachment: {path.name}]"})
            except Exception:
                pass
        return items


__all__ = ["ContextBuilder"]
