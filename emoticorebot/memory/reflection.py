"""Reflection execution and memory persistence helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from emoticorebot.agent.cognitive import CognitiveEvent
from emoticorebot.agent.reflection.deep import DeepReflectionProposal, DeepReflectionResult, DeepReflectionService
from emoticorebot.agent.reflection.input import build_reflection_input
from emoticorebot.agent.reflection.turn import TurnReflectionService
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import RepliedPayload
from emoticorebot.protocol.memory_models import ReflectSignalPayload
from emoticorebot.types import ReflectionInput

from .crystallizer import SkillMaterializer
from .store import MemoryStore


@dataclass(frozen=True, slots=True)
class TurnReflectionProposal:
    reflection_input: ReflectionInput
    turn_reflection: dict[str, Any]
    cognitive_events: list[CognitiveEvent]


class ReflectionManager:
    """Runs reflection services and commits approved memory writes."""

    _SKILL_MEMORY_TYPES = {"skill_hint", "skill"}

    def __init__(
        self,
        *,
        workspace: Path,
        emotion_manager: EmotionStateManager,
        memory_store: MemoryStore,
        turn_reflection: TurnReflectionService,
        deep_reflection: DeepReflectionService,
    ) -> None:
        self._workspace = Path(workspace)
        self._emotion_mgr = emotion_manager
        self._memory_store = memory_store
        self._turn_reflection = turn_reflection
        self._deep_reflection = deep_reflection
        self._skill_materializer = SkillMaterializer(self._workspace, self._memory_store)

    async def propose_turn(
        self,
        *,
        signal: BusEnvelope[ReflectSignalPayload],
        delivery: BusEnvelope[RepliedPayload],
    ) -> TurnReflectionProposal | None:
        reflection_input = self._build_turn_input(signal=signal, delivery=delivery)
        assistant_output = str(
            reflection_input.get("assistant_output", "") or reflection_input.get("output", "") or ""
        ).strip()
        if not assistant_output:
            return None

        user_input = str(reflection_input.get("user_input", "") or "").strip()
        self._emotion_mgr.update_from_conversation(user_input, assistant_output)
        reflection_input["emotion"] = self._emotion_mgr.snapshot()
        execution = reflection_input.get("execution") if isinstance(reflection_input.get("execution"), dict) else None

        result = await self._turn_reflection.reflect_turn(
            user_input=user_input,
            output=assistant_output,
            emotion=reflection_input["emotion"],
            execution=execution,
            source_type=str(reflection_input.get("source_type", "user_turn") or "user_turn"),
        )
        turn_reflection = dict(result.turn_reflection)
        cognitive_events = CognitiveEvent.build_turn_events(
            reflection_input=reflection_input,
            importance=CognitiveEvent.estimate_importance(user_input, assistant_output),
            turn_reflection=turn_reflection,
        )
        return TurnReflectionProposal(
            reflection_input=reflection_input,
            turn_reflection=turn_reflection,
            cognitive_events=cognitive_events,
        )

    def append_turn_events(self, proposal: TurnReflectionProposal) -> list[str]:
        event_ids: list[str] = []
        for event in proposal.cognitive_events:
            CognitiveEvent.append(self._workspace, event)
            event_ids.append(event.id)
        return event_ids

    def append_turn_memories(
        self,
        proposal: TurnReflectionProposal,
        *,
        event_ids: list[str],
    ) -> list[str]:
        records = self._prepare_turn_memory_candidates(
            reflection_input=proposal.reflection_input,
            turn_reflection=proposal.turn_reflection,
            event_ids=event_ids,
        )
        if not records:
            return []
        return self._memory_store.append_many(records)

    async def propose_deep(self, *, events: list[dict[str, Any]]) -> DeepReflectionProposal:
        return await self._deep_reflection.propose(events)

    def append_deep_memories(self, proposal: DeepReflectionProposal) -> DeepReflectionResult:
        memory_ids = self._memory_store.append_many(proposal.memory_candidates)
        skill_hint_count = sum(
            1
            for record in proposal.memory_candidates
            if str(record.get("type", "") or "").strip() in self._SKILL_MEMORY_TYPES
        )
        materialization = self._skill_materializer.materialize_from_memory()
        return DeepReflectionResult(
            summary=proposal.summary,
            memory_ids=memory_ids,
            memory_count=len(memory_ids),
            skill_hint_count=skill_hint_count,
            materialized_skills=list(materialization.skill_names),
            materialized_skill_count=int(materialization.created_count + materialization.updated_count),
            updated_soul=False,
            updated_user=False,
            user_updates=list(proposal.user_updates),
            soul_updates=list(proposal.soul_updates),
        )

    def recent_cognitive_events(self, *, limit: int) -> list[dict[str, Any]]:
        return CognitiveEvent.recent(self._workspace, limit=limit)

    def lookup_memory_records(self, memory_ids: list[str]) -> list[dict[str, Any]]:
        wanted = {str(item).strip() for item in memory_ids if str(item).strip()}
        if not wanted:
            return []
        return [record for record in self._memory_store.read_all() if str(record.get("id", "") or "") in wanted]

    def _build_turn_input(
        self,
        *,
        signal: BusEnvelope[ReflectSignalPayload],
        delivery: BusEnvelope[RepliedPayload],
    ) -> ReflectionInput:
        metadata = signal.payload.metadata if isinstance(signal.payload.metadata, dict) else {}
        payload = metadata.get("reflection_input")
        if not isinstance(payload, Mapping):
            return {}

        raw = dict(payload)
        message = delivery.payload.delivery_message
        nested_metadata = dict(raw.get("metadata", {}) or {})
        nested_metadata.setdefault("delivery_reply_id", delivery.payload.reply_id)
        nested_metadata.setdefault("delivery_message_id", message.message_id)
        if signal.payload.reason:
            nested_metadata.setdefault("reflect_reason", signal.payload.reason)
        raw["metadata"] = nested_metadata

        if not raw.get("channel") and message.channel:
            raw["channel"] = message.channel
        if not raw.get("chat_id") and message.chat_id:
            raw["chat_id"] = message.chat_id
        if not raw.get("session_id") and signal.session_id:
            raw["session_id"] = signal.session_id
        if not raw.get("turn_id") and signal.turn_id:
            raw["turn_id"] = signal.turn_id
        return build_reflection_input(raw)

    def _prepare_turn_memory_candidates(
        self,
        *,
        reflection_input: ReflectionInput,
        turn_reflection: dict[str, Any],
        event_ids: list[str],
    ) -> list[dict[str, Any]]:
        candidates = turn_reflection.get("memory_candidates") if isinstance(turn_reflection, dict) else []
        if not isinstance(candidates, list):
            return []

        session_id = str(reflection_input.get("session_id", "") or "")
        turn_id = str(reflection_input.get("turn_id", "") or "").strip()
        tool_names = self._extract_tool_names(reflection_input)

        prepared: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            if not summary and not content:
                continue
            prepared.append(
                {
                    **item,
                    "source": {
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "event_ids": list(event_ids),
                        "producer": "memory_governor.turn_reflection",
                        "tool_names": tool_names,
                    },
                    "links": {
                        "related_ids": [],
                        "evidence_ids": list(event_ids),
                        "entity_ids": [],
                        "skill_ids": [],
                        "supersedes": [],
                        "invalidates": [],
                    },
                }
            )
        return prepared

    @staticmethod
    def _extract_tool_names(reflection_input: ReflectionInput) -> list[str]:
        names: list[str] = []
        for item in list(reflection_input.get("task_trace", []) or []):
            if not isinstance(item, dict):
                continue
            for key in ("tool_name", "tool", "name", "node"):
                value = str(item.get(key, "") or "").strip()
                if value and value not in names:
                    names.append(value)
        return names[:6]


__all__ = ["ReflectionManager", "TurnReflectionProposal"]
