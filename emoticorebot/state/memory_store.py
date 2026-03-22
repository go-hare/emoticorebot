"""Three-layer memory store and projection store."""

from __future__ import annotations

import re
from pathlib import Path
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

    @property
    def cognitive_path(self) -> Path:
        return self.memory_root / "cognitive_events.jsonl"

    @property
    def long_term_path(self) -> Path:
        return self.memory_root / "memory.jsonl"

    def append_brain_record(self, thread_id: str, payload: dict[str, Any]) -> None:
        append_jsonl(self.path_for_thread_stream(thread_id, "brain.jsonl"), [self.normalize_raw_record(payload)])

    def append_executor_records(self, thread_id: str, records: list[dict[str, Any]]) -> None:
        rows = [self.normalize_raw_record(record) for record in records]
        append_jsonl(self.path_for_thread_stream(thread_id, "executor.jsonl"), rows)

    def recent_brain_records(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_thread_stream(thread_id, "brain.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def recent_executor_records(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_thread_stream(thread_id, "executor.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def append_patch(self, patch: MemoryPatch) -> None:
        if patch.cognitive_append:
            rows = [self.normalize_cognitive_event(item.model_dump()) for item in patch.cognitive_append]
            append_jsonl(self.cognitive_path, rows)
        if patch.long_term_append or patch.user_updates or patch.soul_updates:
            records = list(patch.long_term_append)
            if patch.user_updates or patch.soul_updates:
                records.append(
                    LongTermRecord(
                        summary="projection updates",
                        user_updates=list(dict.fromkeys(patch.user_updates)),
                        soul_updates=list(dict.fromkeys(patch.soul_updates)),
                    )
                )
            rows = [self.normalize_long_term_record(item.model_dump()) for item in records]
            append_jsonl(self.long_term_path, rows)
            self.refresh_vector_mirror()
            self.refresh_projections()

    def build_memory_view(self, thread_id: str, session_id: str, query: str, limit: int = 6) -> MemoryView:
        return MemoryView(
            raw_layer={
                "recent_dialogue": self.recent_brain_records(thread_id, limit),
                "recent_execution": self.recent_executor_records(thread_id, limit),
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
        for candidate in row.memory_candidates:
            item = MemoryCandidate(**candidate.model_dump() if isinstance(candidate, MemoryCandidate) else candidate)
            if not item.memory_id:
                item.memory_id = f"cand_{row.record_id}_{len(normalized_candidates) + 1}"
            normalized_candidates.append(item)
        row.memory_candidates = normalized_candidates
        row.user_updates = list(dict.fromkeys(str(item).strip() for item in row.user_updates if str(item).strip()))
        row.soul_updates = list(dict.fromkeys(str(item).strip() for item in row.soul_updates if str(item).strip()))
        return row.model_dump()

    def flatten_long_term_candidates(self, session_id: str) -> list[dict[str, Any]]:
        rows = read_jsonl(self.long_term_path)
        flattened: list[dict[str, Any]] = []
        for row in rows:
            if session_id and not self.matches_session(row, session_id):
                continue
            for candidate in list(row.get("memory_candidates", []) or []):
                if not isinstance(candidate, dict):
                    continue
                item = dict(candidate)
                item["record_id"] = str(row.get("record_id", "") or "")
                item["record_summary"] = str(row.get("summary", "") or "")
                flattened.append(item)
        return flattened

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
