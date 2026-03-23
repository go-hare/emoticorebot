"""Thin sleep agent for post-turn memory consolidation."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .memory import FileCurrentStateStore, JsonlMemoryStore, LongTermRecord, MemoryCandidate, MemoryPatch, make_id

SleepPlanner = Callable[["SleepDigest"], Awaitable["SleepOutcome"]]


class SleepEvent(BaseModel):
    source: Literal["front", "kernel"]
    event_type: str
    content: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SleepDigest(BaseModel):
    agent_id: str
    conversation_id: str
    user_id: str
    turn_id: str
    latest_user_text: str
    latest_front_reply: str
    front_events: list[SleepEvent] = Field(default_factory=list)
    kernel_events: list[SleepEvent] = Field(default_factory=list)
    long_term_summary: str = ""
    current_state: str = ""


class SleepOutcome(BaseModel):
    summary: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    soul_updates: list[str] = Field(default_factory=list)
    current_state: str = ""
    notes: str = ""


class SleepAgent:
    """Digest one turn, then write stable traces back into memory."""

    def __init__(
        self,
        *,
        memory_store: JsonlMemoryStore,
        current_state_store: FileCurrentStateStore | None = None,
        planner: SleepPlanner | None = None,
        model: Any | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.current_state_store = current_state_store
        self.planner = planner
        self.model = model

    async def run_for_turn(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        latest_user_text: str,
        latest_front_reply: str,
    ) -> SleepOutcome:
        digest = self.build_digest(
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            latest_user_text=latest_user_text,
            latest_front_reply=latest_front_reply,
        )
        if self.planner is not None:
            outcome = await self.planner(digest)
        elif self.model is not None:
            outcome = await self.plan_with_llm(digest)
        else:
            outcome = self.build_default_outcome(digest)
        self.apply_outcome(
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            outcome=outcome,
        )
        return outcome

    def build_digest(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        latest_user_text: str,
        latest_front_reply: str,
    ) -> SleepDigest:
        memory = self.memory_store.build_memory_view(conversation_id, agent_id, latest_user_text)
        front_events = self._build_front_events(memory.raw_layer.get("recent_front_events", [])[-6:])
        kernel_events = self._build_kernel_events(
            dialogue_rows=memory.raw_layer.get("recent_dialogue", [])[-6:],
            tool_rows=memory.raw_layer.get("recent_tools", [])[-4:],
        )
        return SleepDigest(
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            latest_user_text=latest_user_text,
            latest_front_reply=latest_front_reply,
            front_events=front_events,
            kernel_events=kernel_events,
            long_term_summary=str(memory.long_term_layer.get("summary", "") or "").strip(),
            current_state=str(memory.current_state or "").strip(),
        )

    def build_default_outcome(self, digest: SleepDigest) -> SleepOutcome:
        last_front_event = self._latest_event_content(digest.front_events)
        last_kernel_event = self._latest_event_content(digest.kernel_events)
        has_tool_trace = any(event.event_type == "tool" for event in digest.kernel_events)
        summary = self._clip(f"User asked: {digest.latest_user_text}", 160)
        if has_tool_trace and last_kernel_event:
            summary = self._clip(f"{summary}; tools: {last_kernel_event}", 220)
        elif last_kernel_event:
            summary = self._clip(f"{summary}; kernel: {last_kernel_event}", 220)
        elif last_front_event:
            summary = self._clip(f"{summary}; front: {last_front_event}", 220)

        candidate_type = "execution" if has_tool_trace else "relationship"
        detail_parts = []
        if digest.latest_front_reply:
            detail_parts.append(f"front_reply={self._clip(digest.latest_front_reply, 160)}")
        if last_front_event:
            detail_parts.append(f"front_event={self._clip(last_front_event, 160)}")
        if last_kernel_event:
            detail_parts.append(f"kernel_event={self._clip(last_kernel_event, 160)}")

        memory_candidate = MemoryCandidate(
            memory_id=make_id("cand"),
            memory_type=candidate_type,
            summary=self._clip(digest.latest_user_text, 120),
            detail=" | ".join(detail_parts),
            confidence=0.55 if has_tool_trace else 0.45,
            stability=0.35,
            tags=self._extract_tags(" ".join(self._build_tag_inputs(digest))),
            metadata={"source": "sleep_agent"},
        )

        user_updates = self._extract_user_updates(digest.latest_user_text)
        current_state = digest.current_state or "# Current State\n\n- calm\n"
        return SleepOutcome(
            summary=summary,
            memory_candidates=[memory_candidate],
            user_updates=user_updates,
            current_state=current_state,
            notes="heuristic consolidation",
        )

    async def plan_with_llm(self, digest: SleepDigest) -> SleepOutcome:
        messages = self._build_llm_messages(digest)

        try:
            response = await self._invoke_planner(self._bind_structured_planner(self.model), messages)
            outcome = self._normalize_outcome(self._coerce_outcome(response), digest=digest, source="llm_reflection")
            return self._merge_with_heuristic(outcome, digest=digest)
        except Exception as structured_exc:
            try:
                response = await self._invoke_planner(self._bind_json_planner(self.model), messages)
                outcome = self._coerce_outcome(response)
                normalized = self._normalize_outcome(outcome, digest=digest, source="llm_reflection_fallback")
                return self._merge_with_heuristic(normalized, digest=digest)
            except Exception as raw_exc:
                fallback = self.build_default_outcome(digest)
                reason = self._clip(f"{structured_exc} | {raw_exc}", 180)
                fallback.notes = f"heuristic fallback after llm failure: {reason}"
                return fallback

    def apply_outcome(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        outcome: SleepOutcome,
    ) -> None:
        if outcome.current_state and self.current_state_store is not None:
            self.current_state_store.write(outcome.current_state)

        has_memory = bool(outcome.memory_candidates or outcome.user_updates or outcome.soul_updates)
        if not has_memory:
            return

        record = LongTermRecord(
            record_id=make_id("mem"),
            user_id=user_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=outcome.summary or "sleep consolidation",
            memory_candidates=list(outcome.memory_candidates),
            user_updates=list(outcome.user_updates),
            soul_updates=list(outcome.soul_updates),
            source_event_ids=[turn_id],
        )
        self.memory_store.append_patch(MemoryPatch(long_term_append=[record]))

    def _build_front_events(self, rows: list[dict[str, Any]]) -> list[SleepEvent]:
        events: list[SleepEvent] = []
        for row in rows:
            content = str(row.get("content", "") or "").strip()
            if not content:
                continue
            events.append(
                SleepEvent(
                    source="front",
                    event_type=str(row.get("event_type", "") or "front").strip() or "front",
                    content=content,
                    created_at=str(row.get("created_at", "") or "").strip(),
                    metadata={
                        "emotion": str(row.get("emotion", "") or "").strip(),
                        "tags": list(row.get("tags", []) or []),
                    },
                )
            )
        return events

    def _build_kernel_events(
        self,
        *,
        dialogue_rows: list[dict[str, Any]],
        tool_rows: list[dict[str, Any]],
    ) -> list[SleepEvent]:
        events: list[SleepEvent] = []
        for row in dialogue_rows:
            content = str(row.get("content", "") or "").strip()
            if not content:
                continue
            role = str(row.get("role", "") or "dialogue").strip() or "dialogue"
            events.append(
                SleepEvent(
                    source="kernel",
                    event_type=role,
                    content=f"{role}: {content}",
                    created_at=str(row.get("created_at", "") or "").strip(),
                    metadata={"turn_id": str(row.get("turn_id", "") or "").strip()},
                )
            )
        for row in tool_rows:
            content = str(row.get("content", "") or "").strip()
            if not content:
                continue
            tool_name = str(row.get("tool_name", "") or "tool").strip() or "tool"
            events.append(
                SleepEvent(
                    source="kernel",
                    event_type="tool",
                    content=f"{tool_name}: {content}",
                    created_at=str(row.get("created_at", "") or "").strip(),
                    metadata={"tool_name": tool_name},
                )
            )
        events.sort(key=lambda item: item.created_at)
        return events

    def _latest_event_content(self, events: list[SleepEvent]) -> str:
        if not events:
            return ""
        return str(events[-1].content or "").strip()

    def _build_tag_inputs(self, digest: SleepDigest) -> list[str]:
        values = [digest.latest_user_text, digest.latest_front_reply]
        values.extend(event.content for event in digest.front_events)
        values.extend(event.content for event in digest.kernel_events)
        return [str(value or "").strip() for value in values if str(value or "").strip()]

    def _build_llm_messages(self, digest: SleepDigest) -> list[Any]:
        digest_payload = digest.model_dump()
        digest_json = json.dumps(digest_payload, ensure_ascii=False, indent=2)
        return [
            SystemMessage(
                content=(
                    "You are the sleep reflection planner for a companion robot brain.\n"
                    "Read the turn digest and return a concise structured reflection.\n"
                    "Prefer stable, reusable memory only. Avoid fluff. Avoid inventing facts.\n"
                    "Keep memory_candidates to at most 3 items.\n"
                    "Allowed memory_type values: relationship, fact, working, execution, reflection.\n"
                    "current_state must be markdown and compact.\n"
                    "When the user states a stable preference, desire, identity, or naming preference, "
                    "include it in user_updates."
                )
            ),
            HumanMessage(
                content=(
                    "Turn digest JSON:\n"
                    f"{digest_json}\n\n"
                    "Return a JSON object with keys: summary, memory_candidates, user_updates, "
                    "soul_updates, current_state, notes."
                )
            ),
        ]

    def _bind_structured_planner(self, model: Any) -> Any:
        if hasattr(model, "with_structured_output"):
            return model.with_structured_output(SleepOutcome)
        return model

    def _bind_json_planner(self, model: Any) -> Any:
        if hasattr(model, "bind"):
            return model.bind(response_format={"type": "json_object"})
        return model

    async def _invoke_planner(self, model: Any, messages: list[Any]) -> Any:
        if hasattr(model, "ainvoke"):
            return await model.ainvoke(messages)
        if hasattr(model, "invoke"):
            return model.invoke(messages)
        raise RuntimeError("Sleep planner model does not support invoke or ainvoke.")

    def _coerce_outcome(self, response: Any) -> SleepOutcome:
        if isinstance(response, SleepOutcome):
            return response
        if isinstance(response, BaseModel):
            return SleepOutcome.model_validate(response.model_dump())
        if isinstance(response, dict):
            return SleepOutcome.model_validate(response)
        return SleepOutcome.model_validate(self._parse_json_object(self._extract_text(response)))

    def _normalize_outcome(self, outcome: SleepOutcome, *, digest: SleepDigest, source: str) -> SleepOutcome:
        summary = str(outcome.summary or "").strip() or self._clip(f"User asked: {digest.latest_user_text}", 160)
        current_state = str(outcome.current_state or "").strip() or digest.current_state or "# Current State\n\n- calm\n"

        normalized_candidates: list[MemoryCandidate] = []
        for item in list(outcome.memory_candidates)[:3]:
            candidate = MemoryCandidate.model_validate(item)
            if not candidate.memory_id:
                candidate.memory_id = make_id("cand")
            candidate.tags = self._dedupe_strings(candidate.tags)[:8]
            candidate.metadata = dict(candidate.metadata)
            candidate.metadata.setdefault("source", source)
            normalized_candidates.append(candidate)

        return SleepOutcome(
            summary=summary,
            memory_candidates=normalized_candidates,
            user_updates=self._dedupe_strings(outcome.user_updates)[:4],
            soul_updates=self._dedupe_strings(outcome.soul_updates)[:4],
            current_state=current_state,
            notes=str(outcome.notes or source).strip() or source,
        )

    def _merge_with_heuristic(self, outcome: SleepOutcome, *, digest: SleepDigest) -> SleepOutcome:
        heuristic = self.build_default_outcome(digest)
        merged_candidates = list(outcome.memory_candidates) or list(heuristic.memory_candidates)
        merged_user_updates = self._dedupe_strings(list(outcome.user_updates) + list(heuristic.user_updates))[:4]
        merged_soul_updates = self._dedupe_strings(list(outcome.soul_updates) + list(heuristic.soul_updates))[:4]

        if merged_candidates == list(outcome.memory_candidates) and merged_user_updates == list(outcome.user_updates):
            return outcome

        notes = str(outcome.notes or "").strip() or "llm_reflection"
        return SleepOutcome(
            summary=outcome.summary or heuristic.summary,
            memory_candidates=merged_candidates[:3],
            user_updates=merged_user_updates,
            soul_updates=merged_soul_updates,
            current_state=outcome.current_state or heuristic.current_state,
            notes=f"{notes}; heuristic_backfill",
        )

    def _extract_text(self, response: Any) -> str:
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content).strip()

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        value = str(text or "").strip()
        if not value:
            raise ValueError("Sleep planner returned empty content.")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            start = value.find("{")
            end = value.rfind("}")
            if start < 0 or end <= start:
                raise
            payload = json.loads(value[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("Sleep planner response must be a JSON object.")
        return payload

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        rows: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in rows:
                rows.append(text)
        return rows

    def _extract_user_updates(self, user_text: str) -> list[str]:
        text = str(user_text or "").strip()
        if not text:
            return []

        rules = [
            r"(我喜欢[^。！？!\n]+)",
            r"(我不喜欢[^。！？!\n]+)",
            r"(我是[^。！？!\n]+)",
            r"(我想要[^。！？!\n]+)",
            r"(I like[^.!?\n]+)",
            r"(I don't like[^.!?\n]+)",
            r"(I am[^.!?\n]+)",
            r"(I want[^.!?\n]+)",
        ]
        updates: list[str] = []
        for pattern in rules:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                cleaned = str(match).strip()
                if cleaned and cleaned not in updates:
                    updates.append(cleaned)
        return updates[:4]

    def _extract_tags(self, text: str) -> list[str]:
        tokens = [token for token in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if token]
        tags: list[str] = []
        for token in tokens:
            if len(token) < 2:
                continue
            if token not in tags:
                tags.append(token)
            if len(tags) >= 8:
                break
        return tags

    def _clip(self, text: str, limit: int) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."
