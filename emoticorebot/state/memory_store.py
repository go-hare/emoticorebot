"""Three-layer memory store."""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from typing import Any

from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.state.io import append_jsonl, ensure_directory, read_jsonl, read_text, write_text
from emoticorebot.state.schemas import CognitiveEvent, LongTermRecord, MemoryCandidate, MemoryPatch, MemoryView, now_iso
from emoticorebot.state.vector_store import VectorMirror


class MemoryStore:
    """Owns raw, cognitive, long-term, and projection files."""

    def __init__(
        self,
        workspace: Path,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ):
        self.workspace = workspace
        self.memory_root = ensure_directory(self.workspace / "memory")
        self.session_root = ensure_directory(self.workspace / "session")
        self.vector_mirror = VectorMirror(workspace, providers_config, memory_config)
        self.write_lock = Lock()

    @property
    def cognitive_path(self) -> Path:
        return self.memory_root / "cognitive_events.jsonl"

    @property
    def long_term_path(self) -> Path:
        return self.memory_root / "memory.jsonl"

    def append_brain_record(self, thread_id: str, payload: dict[str, Any]) -> None:
        row = self.normalize_raw_record(payload)
        with self.write_lock:
            append_jsonl(self.path_for_thread_stream(thread_id, "brain.jsonl"), [row])

    def append_tool_record(self, thread_id: str, payload: dict[str, Any]) -> None:
        row = self.normalize_raw_record(payload)
        with self.write_lock:
            append_jsonl(self.path_for_thread_stream(thread_id, "tool.jsonl"), [row])

    def recent_brain_records(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_thread_stream(thread_id, "brain.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def recent_tool_records(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_thread_stream(thread_id, "tool.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def append_patch(self, patch: MemoryPatch) -> None:
        cognitive_rows = [self.normalize_cognitive_event(item.model_dump()) for item in patch.cognitive_append]
        long_term_records = list(patch.long_term_append)
        if patch.user_updates or patch.soul_updates:
            long_term_records.append(
                LongTermRecord(
                    summary="projection updates",
                    user_updates=list(dict.fromkeys(patch.user_updates)),
                    soul_updates=list(dict.fromkeys(patch.soul_updates)),
                )
            )
        long_term_rows = [self.normalize_long_term_record(item.model_dump()) for item in long_term_records]

        with self.write_lock:
            if cognitive_rows:
                append_jsonl(self.cognitive_path, cognitive_rows)
            if long_term_rows:
                append_jsonl(self.long_term_path, long_term_rows)

        if long_term_rows:
            self.refresh_vector_mirror()
            self.refresh_projections()

    def build_memory_view(self, thread_id: str, session_id: str, query: str, limit: int = 6) -> MemoryView:
        return MemoryView(
            raw_layer={
                "recent_dialogue": self.recent_brain_records(thread_id, limit),
                "recent_tools": self.recent_tool_records(thread_id, limit),
            },
            cognitive_layer=self.recent_cognitive_events(session_id, limit),
            long_term_layer={
                "summary": self.build_long_term_summary(query=query, session_id=session_id, limit=limit),
                "records": self.query_long_term(query=query, session_id=session_id, limit=limit),
            },
            projections={
                "user_anchor": read_text(self.workspace / "USER.md"),
                "soul_anchor": read_text(self.workspace / "SOUL.md"),
            },
            current_state=read_text(self.workspace / "current_state.md"),
        )

    def recent_cognitive_events(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        rows = [row for row in read_jsonl(self.cognitive_path) if self.matches_session(row, session_id)]
        return rows[-limit:] if limit > 0 else rows

    def query_long_term(self, query: str, session_id: str, limit: int) -> list[dict[str, Any]]:
        candidates = self.flatten_long_term_candidates(session_id=session_id)
        if not candidates:
            return []
        text = str(query or "").strip()
        vector_scores = self.vector_mirror.search(text, limit=max(limit * 2, 8))
        ranked = sorted(candidates, key=lambda item: self.score_candidate(item, text, vector_scores), reverse=True)
        return ranked[:limit] if limit > 0 else ranked

    def build_long_term_summary(self, query: str, session_id: str, limit: int) -> str:
        rows = self.query_long_term(query=query, session_id=session_id, limit=limit)
        lines: list[str] = []
        for row in rows:
            summary = str(row.get("summary", "") or "").strip()
            memory_type = str(row.get("memory_type", "") or "").strip()
            if summary:
                lines.append(f"- [{memory_type}] {summary}")
        return "\n".join(lines)

    def refresh_projections(self) -> None:
        rows = read_jsonl(self.long_term_path)
        user_updates: list[str] = []
        soul_updates: list[str] = []
        for row in rows:
            for item in list(row.get("user_updates", []) or []):
                text = str(item or "").strip()
                if text and text not in user_updates:
                    user_updates.append(text)
            for item in list(row.get("soul_updates", []) or []):
                text = str(item or "").strip()
                if text and text not in soul_updates:
                    soul_updates.append(text)
        write_text(self.workspace / "USER.md", self.render_projection("用户画像", user_updates))
        write_text(self.workspace / "SOUL.md", self.render_projection("灵魂锚点", soul_updates))

    def refresh_vector_mirror(self) -> None:
        candidates = self.flatten_long_term_candidates(session_id="")
        signature = {
            "row_count": len(read_jsonl(self.long_term_path)),
            "candidate_count": len(candidates),
        }
        if self.vector_mirror.is_in_sync(signature):
            return
        self.vector_mirror.rebuild(candidates, signature)

    def path_for_thread_stream(self, thread_id: str, filename: str) -> Path:
        safe_thread = re.sub(r"[^a-zA-Z0-9._-]+", "_", thread_id)
        return ensure_directory(self.session_root / safe_thread) / filename

    def normalize_raw_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        return row

    def normalize_cognitive_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = CognitiveEvent(**payload)
        if not row.event_id:
            row.event_id = f"evt_{row.created_at.replace(':', '').replace('-', '').replace('.', '')}"
        return row.model_dump()

    def normalize_long_term_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = LongTermRecord(**payload)
        if not row.record_id:
            row.record_id = f"mem_{row.created_at.replace(':', '').replace('-', '').replace('.', '')}"
        normalized_candidates: list[MemoryCandidate] = []
        seen_memory_ids: set[str] = set()
        for candidate in row.memory_candidates:
            item = MemoryCandidate(**candidate.model_dump() if isinstance(candidate, MemoryCandidate) else candidate)
            base_memory_id = item.memory_id or f"cand_{row.record_id}_{len(normalized_candidates) + 1}"
            item.memory_id = self.make_unique_memory_id(str(base_memory_id), seen_memory_ids)
            seen_memory_ids.add(item.memory_id)
            normalized_candidates.append(item)
        row.memory_candidates = normalized_candidates
        row.user_updates = list(dict.fromkeys(str(item).strip() for item in row.user_updates if str(item).strip()))
        row.soul_updates = list(dict.fromkeys(str(item).strip() for item in row.soul_updates if str(item).strip()))
        return row.model_dump()

    def flatten_long_term_candidates(self, session_id: str) -> list[dict[str, Any]]:
        rows = read_jsonl(self.long_term_path)
        flattened_by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            if session_id and not self.matches_session(row, session_id):
                continue
            for index, candidate in enumerate(list(row.get("memory_candidates", []) or []), start=1):
                if not isinstance(candidate, dict):
                    continue
                item = dict(candidate)
                memory_id = str(item.get("memory_id", "") or "").strip() or f"cand_{row.get('record_id', 'mem')}_{index}"
                item["memory_id"] = memory_id
                item["record_id"] = str(row.get("record_id", "") or "")
                item["record_summary"] = str(row.get("summary", "") or "")
                flattened_by_id[memory_id] = item
        return list(flattened_by_id.values())

    def score_candidate(self, candidate: dict[str, Any], query: str, vector_scores: dict[str, float]) -> float:
        tokens = self.tokenize(query)
        text = " ".join(
            [
                str(candidate.get("summary", "") or ""),
                str(candidate.get("detail", "") or ""),
                " ".join(str(item).strip() for item in list(candidate.get("tags", []) or []) if str(item).strip()),
            ]
        )
        overlap = 0.0
        if tokens:
            candidate_tokens = self.tokenize(text)
            if candidate_tokens:
                overlap = len(tokens & candidate_tokens) / max(1, len(tokens))
        confidence = float(candidate.get("confidence", 0.0) or 0.0)
        stability = float(candidate.get("stability", 0.0) or 0.0)
        vector_score = float(vector_scores.get(str(candidate.get("memory_id", "") or ""), 0.0))
        if not tokens:
            overlap = 0.25
        return (overlap * 0.5) + (confidence * 0.2) + (stability * 0.1) + (vector_score * 0.2)

    def tokenize(self, text: str) -> set[str]:
        return {token for token in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if token}

    def matches_session(self, row: dict[str, Any], session_id: str) -> bool:
        if not session_id:
            return True
        row_session = str(row.get("session_id", "") or "").strip()
        return not row_session or row_session == session_id

    def render_projection(self, title: str, rows: list[str]) -> str:
        lines = [f"# {title}", ""]
        if not rows:
            lines.append("- 暂无稳定沉淀")
            return "\n".join(lines) + "\n"
        lines.extend(f"- {item}" for item in rows)
        return "\n".join(lines) + "\n"

    def make_unique_memory_id(self, base_id: str, seen_memory_ids: set[str]) -> str:
        candidate = str(base_id or "").strip() or "memory_candidate"
        if candidate not in seen_memory_ids:
            return candidate
        suffix = 2
        while f"{candidate}_{suffix}" in seen_memory_ids:
            suffix += 1
        return f"{candidate}_{suffix}"

