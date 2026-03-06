"""Memory system for persistent agent memory."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from emoticorebot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from emoticorebot.session.manager import Session


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "description": (
                            "List of individual objective facts extracted from the conversation. "
                            "Each fact is stored as a separate vector entry for semantic retrieval. "
                            "Extract ALL meaningful facts, each as a single self-contained sentence."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                                "category": {
                                    "type": "string",
                                    "enum": ["preference", "project", "habit", "event", "other"],
                                },
                            },
                            "required": ["text", "importance"],
                        },
                    },
                    "history_entry": {"type": "string"},
                    "memory_update": {"type": "string"},
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


class MemoryStore:
    """Long-term memory files store: MEMORY.md + HISTORY.md."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self, query: str = "", max_chars: int = 2000) -> str:
        long_term = self.read_long_term()
        if not long_term:
            return ""
        if len(long_term) <= max_chars or not query:
            return f"## Long-term Memory\n{long_term}"

        paragraphs = self._split_by_section(long_term)
        query_words = set(query.lower().split())
        scored = [(len(query_words & set(p.lower().split())), p) for p in paragraphs]
        scored.sort(key=lambda x: x[0], reverse=True)
        selected, total = [], 0
        for _, para in scored:
            if total + len(para) > max_chars:
                break
            selected.append(para)
            total += len(para)
        result = "\n\n".join(selected)
        return f"## Long-term Memory\n{result}" if result else ""

    def get_relevant_history(self, query: str = "", k: int = 5) -> str:
        if not self.history_file.exists():
            return ""
        raw = self.history_file.read_text(encoding="utf-8")
        entries = [e.strip() for e in raw.split("\n\n") if e.strip()]
        if not entries:
            return ""

        query_words = set(query.lower().split()) if query else set()
        now = datetime.now()
        ts_pattern = re.compile(r"\[(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})\]")
        scored = []
        for entry in entries:
            m = ts_pattern.search(entry)
            if m:
                try:
                    ts = datetime.fromisoformat(m.group(1).replace(" ", "T"))
                    hours = (now - ts).total_seconds() / 3600
                    recency = 0.99 ** hours
                except ValueError:
                    recency = 0.5
            else:
                recency = 0.3
            if query_words:
                entry_words = set(entry.lower().split())
                overlap = len(query_words & entry_words)
                relevance = min(overlap / max(len(query_words), 1), 1.0)
            else:
                relevance = 0.5
            scored.append((0.4 * recency + 0.6 * relevance, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return "\n\n".join(e for _, e in scored[:k])

    @staticmethod
    def _split_by_section(text: str) -> list[str]:
        sections = re.split(r"\n(?=##)", text)
        return [s.strip() for s in sections if s.strip()]

    async def consolidate(
        self,
        session: "Session",
        llm: "BaseChatModel",
        *,
        archive_all: bool = False,
        memory_window: int = 50,
        cold_store: "Any | None" = None,
    ) -> bool:
        if archive_all:
            old_messages = session.messages
            keep_count = 0
        else:
            keep_count = memory_window // 2
            if len(session.messages) <= keep_count:
                return True
            if len(session.messages) - session.last_consolidated <= 0:
                return True
            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return True

        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.
Focus on extracting objective facts about the user (preferences, projects, habits) for long-term memory.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            
            # 使用 LangChain 直接调用
            llm_with_tools = llm.bind_tools(_SAVE_MEMORY_TOOL)
            response = await llm_with_tools.ainvoke([
                SystemMessage(content="You are a memory consolidation agent."),
                HumanMessage(content=prompt),
            ])
            
            # 提取工具调用
            if not hasattr(response, "tool_calls") or not response.tool_calls:
                return False
            
            tool_call = response.tool_calls[0]
            args = tool_call.get("args", {})
            if not isinstance(args, dict):
                return False

            if entry := args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                self.append_history(entry)
            if update := args.get("memory_update"):
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    self.write_long_term(update)

            if cold_store is not None:
                facts = args.get("facts", [])
                if isinstance(facts, list):
                    for fact in facts:
                        if not isinstance(fact, dict):
                            continue
                        text = fact.get("text", "")
                        if not text:
                            continue
                        cold_store.save(
                            text=text,
                            importance=int(fact.get("importance", 5)),
                            category=str(fact.get("category", "other")),
                        )
                    if facts:
                        logger.info("SemanticStore: {} facts written", len(facts))

            session.last_consolidated = 0 if archive_all else len(session.messages) - keep_count
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return False

