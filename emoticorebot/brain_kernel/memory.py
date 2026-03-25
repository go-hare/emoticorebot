"""Main memory trunk: Letta-style blocks plus minimal local storage."""

from __future__ import annotations

import json
import re
from datetime import datetime
from io import StringIO
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CORE_MEMORY_BLOCK_CHAR_LIMIT: int = 100000
DEFAULT_PERSONA_BLOCK_DESCRIPTION = (
    "The persona block: Stores details about your current persona, guiding how you behave and respond. "
    "This helps you to maintain consistency and personality in your interactions."
)
DEFAULT_HUMAN_BLOCK_DESCRIPTION = (
    "The human block: Stores key details about the person you are conversing with, allowing for more "
    "personalized and friend-like conversation."
)
CORE_MEMORY_LINE_NUMBER_WARNING = (
    "# NOTE: Line numbers shown below (with arrows like '1→') are to help during editing. "
    "Do NOT include line number prefixes in your memory edit tool calls."
)
INT32_MAX = 2147483647


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ensure_directory(path.parent)
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb+") as handle:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                handle.seek(0, 2)
                handle.write(b"\n")
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")


class MemoryCandidate(BaseModel):
    memory_id: str = ""
    memory_type: Literal["relationship", "fact", "working", "execution", "reflection"] = "fact"
    summary: str = ""
    detail: str = ""
    confidence: float = 0.0
    stability: float = 0.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LongTermRecord(BaseModel):
    record_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    summary: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class CognitiveEvent(BaseModel):
    event_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    summary: str = ""
    outcome: str = "unknown"
    reason: str = ""
    needs_deep_reflection: bool = False
    user_text: str = ""
    assistant_text: str = ""
    source_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class MemoryPatch(BaseModel):
    cognitive_append: list[CognitiveEvent] = Field(default_factory=list)
    long_term_append: list[LongTermRecord] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)


class MemoryView(BaseModel):
    raw_layer: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    cognitive_layer: list[dict[str, Any]] = Field(default_factory=list)
    long_term_layer: dict[str, Any] = Field(default_factory=dict)
    projections: dict[str, str] = Field(default_factory=dict)


class JsonlMemoryStore:
    """Minimal self-contained memory store for the main trunk."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.memory_root = ensure_directory(self.workspace / "memory")
        self.session_root = ensure_directory(self.workspace / "session")
        self.write_lock = Lock()

    @property
    def long_term_path(self) -> Path:
        return self.memory_root / "memory.jsonl"

    @property
    def cognitive_path(self) -> Path:
        return self.memory_root / "cognitive_events.jsonl"

    def append_brain_record(self, conversation_id: str, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        with self.write_lock:
            append_jsonl(self.path_for_conversation_stream(conversation_id, "brain.jsonl"), [row])

    def append_tool_record(self, conversation_id: str, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        with self.write_lock:
            append_jsonl(self.path_for_conversation_stream(conversation_id, "tool.jsonl"), [row])

    def append_front_record(self, conversation_id: str, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        with self.write_lock:
            append_jsonl(self.path_for_conversation_stream(conversation_id, "front.jsonl"), [row])

    def append_patch(self, patch: MemoryPatch) -> None:
        cognitive_rows = [self.normalize_cognitive_event(item.model_dump()) for item in patch.cognitive_append]
        long_term_rows = [self.normalize_long_term_record(item.model_dump()) for item in patch.long_term_append]
        if patch.user_updates or patch.soul_updates:
            long_term_rows.append(
                self.normalize_long_term_record(
                    LongTermRecord(
                        record_id=make_id("mem"),
                        summary="projection updates",
                        user_updates=list(dict.fromkeys(patch.user_updates)),
                        soul_updates=list(dict.fromkeys(patch.soul_updates)),
                    ).model_dump()
                )
            )

        with self.write_lock:
            if cognitive_rows:
                append_jsonl(self.cognitive_path, cognitive_rows)
            if long_term_rows:
                append_jsonl(self.long_term_path, long_term_rows)

        if long_term_rows:
            self.refresh_projections()

    def build_memory_view(self, conversation_id: str, agent_id: str, query: str, limit: int = 6) -> MemoryView:
        return MemoryView(
            raw_layer={
                "recent_dialogue": self.recent_brain_records(conversation_id, limit),
                "recent_front_events": self.recent_front_records(conversation_id, limit),
                "recent_tools": self.recent_tool_records(conversation_id, limit),
            },
            cognitive_layer=self.recent_cognitive_events(conversation_id, limit),
            long_term_layer={
                "summary": self.build_long_term_summary(query=query, agent_id=agent_id, limit=limit),
                "records": self.query_long_term(query=query, agent_id=agent_id, limit=limit),
            },
            projections={
                "user_anchor": read_text(self.workspace / "USER.md"),
                "soul_anchor": read_text(self.workspace / "SOUL.md"),
                "front_anchor": read_text(self.workspace / "FRONT.md"),
            },
        )

    def recent_brain_records(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_conversation_stream(conversation_id, "brain.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def recent_front_records(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_conversation_stream(conversation_id, "front.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def recent_tool_records(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_conversation_stream(conversation_id, "tool.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def recent_cognitive_events(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = [row for row in read_jsonl(self.cognitive_path) if self.matches_conversation(row, conversation_id)]
        return rows[-limit:] if limit > 0 else rows

    def query_long_term(self, query: str, agent_id: str, limit: int) -> list[dict[str, Any]]:
        rows = []
        tokens = self.tokenize(query)
        for row in read_jsonl(self.long_term_path):
            if not self.matches_agent(row, agent_id):
                continue
            for candidate in row.get("memory_candidates", []) or []:
                if not isinstance(candidate, dict):
                    continue
                payload = dict(candidate)
                payload["record_summary"] = str(row.get("summary", "") or "")
                score = self.score_candidate(payload, tokens)
                rows.append((score, payload))

        rows.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in rows[:limit]]

    def build_long_term_summary(self, query: str, agent_id: str, limit: int) -> str:
        rows = self.query_long_term(query=query, agent_id=agent_id, limit=limit)
        lines = []
        for row in rows:
            summary = str(row.get("summary", "") or "").strip()
            memory_type = str(row.get("memory_type", "") or "").strip()
            if summary:
                lines.append(f"- [{memory_type}] {summary}")
        return "\n".join(lines)

    def path_for_conversation_stream(self, conversation_id: str, filename: str) -> Path:
        safe_conversation = re.sub(r"[^a-zA-Z0-9._-]+", "_", conversation_id)
        return ensure_directory(self.session_root / safe_conversation) / filename

    def matches_conversation(self, row: dict[str, Any], conversation_id: str) -> bool:
        if not conversation_id:
            return True
        row_conversation = str(row.get("conversation_id", "") or "").strip()
        return not row_conversation or row_conversation == conversation_id

    def matches_agent(self, row: dict[str, Any], agent_id: str) -> bool:
        if not agent_id:
            return True
        row_agent = str(row.get("agent_id", "") or "").strip()
        return not row_agent or row_agent == agent_id

    def normalize_cognitive_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = CognitiveEvent.model_validate(payload)
        if not row.event_id:
            row.event_id = make_id("cog")
        return row.model_dump()

    def normalize_long_term_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = LongTermRecord.model_validate(payload)
        if not row.record_id:
            row.record_id = make_id("mem")
        return row.model_dump()

    def refresh_projections(self) -> None:
        user_updates: list[str] = []
        soul_updates: list[str] = []
        for row in read_jsonl(self.long_term_path):
            for item in row.get("user_updates", []) or []:
                text = str(item or "").strip()
                if text and text not in user_updates:
                    user_updates.append(text)
            for item in row.get("soul_updates", []) or []:
                text = str(item or "").strip()
                if text and text not in soul_updates:
                    soul_updates.append(text)

        # Merge stable updates into projection files without overwriting existing content.
        if user_updates:
            self._merge_projection_file(self.workspace / "USER.md", "用户画像", user_updates)
        if soul_updates:
            self._merge_projection_file(self.workspace / "SOUL.md", "灵魂锚点", soul_updates)

    def render_projection(self, title: str, rows: list[str]) -> str:
        lines = [f"# {title}", ""]
        if not rows:
            lines.append("- 暂无稳定沉淀")
            return "\n".join(lines) + "\n"
        lines.extend(f"- {item}" for item in rows)
        return "\n".join(lines) + "\n"

    def _merge_projection_file(self, path: Path, title: str, updates: list[str]) -> None:
        normalized = [str(item or "").strip() for item in updates]
        normalized = [item for item in normalized if item]
        if not normalized:
            return

        existing = read_text(path)
        if not existing.strip():
            write_text(path, self.render_projection(title, list(dict.fromkeys(normalized))))
            return

        existing_bullets = self._extract_bullet_rows(existing)
        to_append = [item for item in normalized if item not in existing_bullets]
        if not to_append:
            return

        lines = existing.splitlines()
        filtered_lines: list[str] = []
        placeholder_removed = False
        for line in lines:
            if not placeholder_removed and line.strip() == "- 暂无稳定沉淀":
                placeholder_removed = True
                continue
            filtered_lines.append(line)

        merged = "\n".join(filtered_lines).rstrip()
        if merged:
            merged += "\n"
        merged += "".join(f"- {item}\n" for item in to_append)
        write_text(path, merged)

    def _extract_bullet_rows(self, content: str) -> set[str]:
        rows: set[str] = set()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                value = stripped[2:].strip()
                if value:
                    rows.add(value)
        return rows

    def score_candidate(self, candidate: dict[str, Any], tokens: set[str]) -> float:
        haystack = " ".join(
            [
                str(candidate.get("summary", "") or ""),
                str(candidate.get("detail", "") or ""),
                " ".join(str(item).strip() for item in candidate.get("tags", []) or [] if str(item).strip()),
            ]
        )
        candidate_tokens = self.tokenize(haystack)
        overlap = 0.25 if not tokens else len(tokens & candidate_tokens) / max(1, len(tokens))
        confidence = float(candidate.get("confidence", 0.0) or 0.0)
        stability = float(candidate.get("stability", 0.0) or 0.0)
        return (overlap * 0.6) + (confidence * 0.25) + (stability * 0.15)

    def tokenize(self, text: str) -> set[str]:
        return {token for token in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if token}


class Block(BaseModel, validate_assignment=True):
    """A reserved section of the context window, kept close to Letta's block semantics."""

    id: str = Field(default_factory=lambda: make_id("block"))
    value: str = Field(..., description="Value of the block.")
    limit: int = Field(CORE_MEMORY_BLOCK_CHAR_LIMIT, description="Character limit of the block.")
    label: str = Field(..., description="Label of the block in the context window.")
    description: str = Field(default="", description="Description of the block.")
    read_only: bool = Field(False, description="Whether the block is read-only.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata of the block.")
    tags: list[str] = Field(default_factory=list, description="The tags associated with the block.")

    model_config = ConfigDict(extra="ignore")

    @field_validator("limit", mode="after")
    @classmethod
    def validate_limit_int32(cls, value: int) -> int:
        if value > INT32_MAX:
            raise ValueError(f"limit must be <= {INT32_MAX} (int32 max), got {value}")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def sanitize_value_null_bytes(cls, value):
        if isinstance(value, str):
            return value.replace("\x00", "")
        return value

    @model_validator(mode="before")
    @classmethod
    def verify_char_limit(cls, data: Any) -> Any:
        if isinstance(data, dict):
            limit = data.get("limit")
            value = data.get("value")
            if limit is not None and value is not None and isinstance(value, str) and len(value) > limit:
                raise ValueError(f"Edit failed: Exceeds {limit} character limit (requested {len(value)})")
        return data

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "value":
            self.__class__.model_validate(self.model_dump(exclude_unset=True))


class Human(Block):
    label: str = "human"
    description: str = DEFAULT_HUMAN_BLOCK_DESCRIPTION


class Persona(Block):
    label: str = "persona"
    description: str = DEFAULT_PERSONA_BLOCK_DESCRIPTION


class Memory(BaseModel, validate_assignment=True):
    """Minimal Letta-style memory with labelled blocks."""

    git_enabled: bool = Field(False, description="Whether this memory renders as file-style blocks.")
    blocks: list[Block] = Field(default_factory=list, description="Memory blocks contained in the in-context memory.")

    def _render_memory_blocks_standard(self, handle: StringIO):
        if len(self.blocks) == 0:
            handle.write("")
            return

        handle.write("<memory_blocks>\nThe following memory blocks are currently engaged in your core memory unit:\n\n")
        for index, block in enumerate(self.blocks):
            chars_current = len(block.value or "")
            limit = block.limit if block.limit is not None else 0
            handle.write(f"<{block.label}>\n")
            handle.write("<description>\n")
            handle.write(f"{block.description}\n")
            handle.write("</description>\n")
            handle.write("<metadata>")
            if block.read_only:
                handle.write("\n- read_only=true")
            handle.write(f"\n- chars_current={chars_current}")
            handle.write(f"\n- chars_limit={limit}\n")
            handle.write("</metadata>\n")
            handle.write("<value>\n")
            handle.write(f"{block.value}\n")
            handle.write("</value>\n")
            handle.write(f"</{block.label}>\n")
            if index != len(self.blocks) - 1:
                handle.write("\n")
        handle.write("\n</memory_blocks>")

    def _render_memory_blocks_line_numbered(self, handle: StringIO):
        handle.write("<memory_blocks>\nThe following memory blocks are currently engaged in your core memory unit:\n\n")
        for index, block in enumerate(self.blocks):
            handle.write(f"<{block.label}>\n")
            handle.write("<description>\n")
            handle.write(f"{block.description}\n")
            handle.write("</description>\n")
            handle.write("<metadata>")
            if block.read_only:
                handle.write("\n- read_only=true")
            handle.write(f"\n- chars_current={len(block.value)}")
            handle.write(f"\n- chars_limit={block.limit}\n")
            handle.write("</metadata>\n")
            handle.write(f"<warning>\n{CORE_MEMORY_LINE_NUMBER_WARNING}\n</warning>\n")
            handle.write("<value>\n")
            for line_index, line in enumerate(block.value.split("\n"), start=1):
                handle.write(f"{line_index}→ {line}\n")
            handle.write("</value>\n")
            handle.write(f"</{block.label}>\n")
            if index != len(self.blocks) - 1:
                handle.write("\n")
        handle.write("\n</memory_blocks>")

    def compile(self, tool_usage_rules: str | None = None, line_numbered: bool = False) -> str:
        handle = StringIO()
        if line_numbered:
            self._render_memory_blocks_line_numbered(handle)
        else:
            self._render_memory_blocks_standard(handle)

        if tool_usage_rules:
            handle.write("\n\n<tool_usage_rules>\n")
            handle.write(f"{tool_usage_rules}\n")
            handle.write("</tool_usage_rules>")

        return handle.getvalue()

    def render(self, tool_usage_rules: str | None = None, line_numbered: bool = False) -> str:
        return self.compile(tool_usage_rules=tool_usage_rules, line_numbered=line_numbered)

    def list_block_labels(self) -> list[str]:
        return [block.label for block in self.blocks]

    def get_block(self, label: str) -> Block:
        keys = []
        for block in self.blocks:
            if block.label == label:
                return block
            keys.append(block.label)
        raise KeyError(f"Block field {label} does not exist (available sections = {', '.join(keys)})")

    def get_blocks(self) -> list[Block]:
        return self.blocks

    def create_block(
        self,
        *,
        label: str,
        value: str = "",
        description: str = "",
        limit: int = CORE_MEMORY_BLOCK_CHAR_LIMIT,
        read_only: bool = False,
    ) -> Block:
        block = Block(label=label, value=value, description=description, limit=limit, read_only=read_only)
        self.blocks.append(block)
        return block

    def set_block(self, block: Block) -> None:
        for index, existing in enumerate(self.blocks):
            if existing.label == block.label:
                self.blocks[index] = block
                return
        self.blocks.append(block)

    def update_block_value(self, label: str, value: str) -> None:
        if not isinstance(value, str):
            raise ValueError("Provided value must be a string")

        for block in self.blocks:
            if block.label == label:
                block.value = value
                return
        raise ValueError(f"Block with label {label} does not exist")


class BasicBlockMemory(Memory):
    def __init__(self, blocks: list[Block] | None = None):
        super().__init__(blocks=blocks or [])


class ChatMemory(BasicBlockMemory):
    def __init__(self, persona: str, human: str, limit: int = CORE_MEMORY_BLOCK_CHAR_LIMIT):
        super().__init__(
            blocks=[
                Block(value=persona, limit=limit, label="persona"),
                Block(value=human, limit=limit, label="human"),
            ]
        )


class CoreMemory(Memory):
    """Main in-context memory assembled from stored projections and recent traces."""

    @classmethod
    def from_memory_view(cls, memory: MemoryView) -> "CoreMemory":
        blocks: list[Block] = []
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        long_term_summary = str(memory.long_term_layer.get("summary", "") or "").strip()
        cognitive_summary = cls._render_cognitive(memory.cognitive_layer)
        recent_dialogue = cls._render_rows(memory.raw_layer.get("recent_dialogue", []), key="role")
        recent_front_events = cls._render_rows(memory.raw_layer.get("recent_front_events", []), key="event_type")
        recent_tools = cls._render_rows(memory.raw_layer.get("recent_tools", []), key="tool_name")

        if user_anchor:
            blocks.append(Human(value=user_anchor))
        if soul_anchor:
            blocks.append(Persona(value=soul_anchor))
        if cognitive_summary:
            blocks.append(Block(label="cognitive_memory", value=cognitive_summary, description="Recent cognitive summaries"))
        if long_term_summary:
            blocks.append(Block(label="summary_memory", value=long_term_summary, description="Retrieved long-term memory"))
        if recent_dialogue:
            blocks.append(Block(label="recent_dialogue", value=recent_dialogue, description="Recent conversation context"))
        if recent_front_events:
            blocks.append(
                Block(
                    label="front_events",
                    value=recent_front_events,
                    description="Recent front/persona layer events",
                )
            )
        if recent_tools:
            blocks.append(Block(label="recent_tools", value=recent_tools, description="Recent tool results"))

        return cls(blocks=blocks)

    @staticmethod
    def _render_rows(rows: list[dict[str, object]], key: str) -> str:
        lines: list[str] = []
        for row in rows[-6:]:
            label = str(row.get(key, "") or "").strip() or "unknown"
            content = str(row.get("content", "") or "").strip()
            if content:
                lines.append(f"{label}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _render_cognitive(rows: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for row in rows[-6:]:
            summary = str(row.get("summary", "") or "").strip()
            outcome = str(row.get("outcome", "") or "").strip()
            if summary and outcome:
                lines.append(f"- [{outcome}] {summary}")
            elif summary:
                lines.append(f"- {summary}")
        return "\n".join(lines)
