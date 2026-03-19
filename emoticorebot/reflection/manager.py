"""Reflection execution and memory persistence helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from emoticorebot.memory.crystallizer import SkillMaterializer
from emoticorebot.memory.store import MemoryStore
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.reflection_models import ReflectionSignalPayload
from emoticorebot.reflection.cognitive import CognitiveEvent
from emoticorebot.reflection.deep import DeepReflectionProposal, DeepReflectionResult, DeepReflectionService
from emoticorebot.reflection.input import build_reflection_input
from emoticorebot.reflection.turn import TurnReflectionService
from emoticorebot.types import ReflectionInput


@dataclass(frozen=True, slots=True)
class TurnReflectionProposal:
    reflection_input: ReflectionInput
    turn_reflection: dict[str, Any]
    cognitive_events: list[CognitiveEvent]


class ReflectionManager:
    """Runs reflection services and commits approved memory writes."""

    _SKILL_MEMORY_SUBTYPES = {"skill_hint", "skill"}

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
        signal: BusEnvelope[ReflectionSignalPayload],
    ) -> TurnReflectionProposal | None:
        reflection_input = self._build_reflection_input(signal=signal)
        assistant_output = str(
            reflection_input.get("assistant_output", "") or reflection_input.get("output", "") or ""
        ).strip()
        if not assistant_output:
            return None

        user_input = str(reflection_input.get("user_input", "") or "").strip()
        self._emotion_mgr.update_from_conversation(user_input, assistant_output)
        reflection_input["emotion"] = self._emotion_mgr.snapshot()
        execution = reflection_input.get("execution") if isinstance(reflection_input.get("execution"), dict) else None

        result = await self._turn_reflection.run_turn_reflection(
            user_input=user_input,
            output=assistant_output,
            emotion=reflection_input["emotion"],
            execution=execution,
            source_type=str(reflection_input.get("source_type", "user_turn") or "user_turn"),
            task=reflection_input.get("task") if isinstance(reflection_input.get("task"), dict) else {},
            task_trace=[
                item for item in list(reflection_input.get("task_trace", []) or []) if isinstance(item, dict)
            ],
            metadata=reflection_input.get("metadata") if isinstance(reflection_input.get("metadata"), dict) else {},
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
        records = [
            *list(proposal.memory_candidates),
            *self._build_deep_update_records(target="user_model", updates=proposal.user_updates),
            *self._build_deep_update_records(target="persona", updates=proposal.soul_updates),
        ]
        memory_ids = self._memory_store.append_many(records)
        skill_hint_count = sum(
            1
            for record in proposal.memory_candidates
            if str((record.get("metadata", {}) or {}).get("subtype", "") or "").strip() in self._SKILL_MEMORY_SUBTYPES
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

    @staticmethod
    def _build_deep_update_records(*, target: str, updates: list[str]) -> list[dict[str, Any]]:
        normalized = ReflectionManager._normalize_text_list(updates)
        if not normalized:
            return []

        if target == "user_model":
            memory_type = "user_model"
            confidence = 0.86
            stability = 0.9
            tags = ["user_model", "deep_reflection"]
            subtype = "user_model"
            importance = 7
        else:
            memory_type = "persona"
            confidence = 0.84
            stability = 0.92
            tags = ["persona", "deep_reflection"]
            subtype = "persona"
            importance = 7

        records: list[dict[str, Any]] = []
        for text in normalized:
            records.append(
                {
                    "memory_type": memory_type,
                    "summary": text,
                    "detail": text,
                    "confidence": confidence,
                    "stability": stability,
                    "tags": tags,
                    "source_module": "reflection_governor.deep_reflection",
                    "metadata": {
                        "subtype": subtype,
                        "importance": importance,
                        "scope": "deep",
                        "source": "deep_reflection_updates",
                    },
                }
            )
        return records

    def recent_cognitive_events(self, *, limit: int) -> list[dict[str, Any]]:
        return CognitiveEvent.recent(self._workspace, limit=limit)

    def lookup_memory_records(self, memory_ids: list[str]) -> list[dict[str, Any]]:
        wanted = {str(item).strip() for item in memory_ids if str(item).strip()}
        if not wanted:
            return []
        return [record for record in self._memory_store.read_all() if str(record.get("memory_id", "") or "") in wanted]

    def _build_reflection_input(self, *, signal: BusEnvelope[ReflectionSignalPayload]) -> ReflectionInput:
        metadata = signal.payload.metadata if isinstance(signal.payload.metadata, dict) else {}
        right_brain = self._build_right_brain_input(signal=signal, metadata=metadata)
        if right_brain:
            return right_brain

        return self._build_left_reflection_input(signal=signal, metadata=metadata)

    def _build_left_reflection_input(
        self,
        *,
        signal: BusEnvelope[ReflectionSignalPayload],
        metadata: Mapping[str, Any],
    ) -> ReflectionInput:
        payload = metadata.get("reflection_input")
        if not isinstance(payload, Mapping):
            return {}

        raw = dict(payload)
        nested_metadata = dict(raw.get("metadata", {}) or {})
        if signal.payload.reason:
            nested_metadata.setdefault("reflection_reason", signal.payload.reason)
        raw["metadata"] = nested_metadata

        if not raw.get("session_id") and signal.session_id:
            raw["session_id"] = signal.session_id
        if not raw.get("turn_id") and signal.turn_id:
            raw["turn_id"] = signal.turn_id
        return build_reflection_input(raw)

    def _build_right_brain_input(
        self,
        *,
        signal: BusEnvelope[ReflectionSignalPayload],
        metadata: Mapping[str, Any],
    ) -> ReflectionInput:
        payload = metadata.get("right_brain_summary")
        if not isinstance(payload, Mapping):
            return {}

        summary = dict(payload)
        origin = summary.get("origin_message")
        origin_message = dict(origin) if isinstance(origin, Mapping) else {}
        trace_items = self._normalize_trace_items(summary.get("task_trace") or summary.get("trace"))
        recent_turns = self._normalize_turns(summary.get("recent_turns"))
        short_term = self._normalize_text_list(summary.get("short_term_memory"))
        long_term = self._normalize_text_list(summary.get("long_term_memory"))
        memory_refs = self._normalize_text_list(summary.get("memory_refs"))
        tool_context = summary.get("tool_context")
        tool_context_payload = dict(tool_context) if isinstance(tool_context, Mapping) else {}
        output = str(
            summary.get("result_text", "") or summary.get("summary", "") or summary.get("error", "")
        ).strip()
        if not output:
            return {}

        result = str(summary.get("result", "") or "").strip()
        execution_status = "failed" if result in {"failed", "cancelled"} else "done"
        failure_reason = (
            str(summary.get("cancel_reason", "") or "").strip()
            or str(summary.get("error", "") or "").strip()
        )

        raw = {
            "session_id": str(signal.session_id or summary.get("session_id", "") or ""),
            "turn_id": str(signal.turn_id or summary.get("turn_id", "") or ""),
            "message_id": str(origin_message.get("message_id", "") or ""),
            "source_type": "task_event",
            "user_input": str(summary.get("request_text", "") or ""),
            "output": output,
            "assistant_output": output,
            "channel": str(origin_message.get("channel", "") or ""),
            "chat_id": str(origin_message.get("chat_id", "") or ""),
            "task": dict(summary.get("task", {}) or {}),
            "task_trace": trace_items,
            "execution": {
                "invoked": True,
                "status": execution_status,
                "summary": str(summary.get("summary", "") or output),
                "failure_reason": failure_reason,
            },
            "metadata": {
                "source_event_type": summary.get("source_event_type"),
                "decision": summary.get("decision"),
                "result": result,
            "reflection_reason": str(signal.payload.reason or ""),
                "recent_turns": recent_turns,
                "short_term_memory": short_term,
                "long_term_memory": long_term,
                "memory_refs": memory_refs,
                "tool_context": tool_context_payload,
                "tool_usage_summary": self._normalize_tool_usage(summary.get("tool_usage_summary")),
                "trace_count": len(trace_items),
            },
        }
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
            detail = str(item.get("detail", "") or "").strip()
            if not summary and not detail:
                continue
            prepared.append(
                {
                    **item,
                    "session_id": session_id,
                    "memory_type": str(item.get("memory_type", "") or "").strip() or "reflection",
                    "detail": detail or summary,
                    "source_module": "reflection_governor.turn_reflection",
                    "source_event_ids": list(event_ids),
                    "evidence_messages": self._evidence_messages(reflection_input),
                    "metadata": {
                        **dict(item.get("metadata", {}) or {}),
                        "tool_names": tool_names,
                    },
                }
            )
        return prepared

    @staticmethod
    def _evidence_messages(reflection_input: ReflectionInput) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []

        user_input = str(reflection_input.get("user_input", "") or "").strip()
        if user_input:
            evidence.append(
                {
                    "role": "user",
                    "content": user_input,
                    "content_blocks": [{"type": "text", "text": user_input}],
                    "session_id": str(reflection_input.get("session_id", "") or ""),
                    "turn_id": str(reflection_input.get("turn_id", "") or ""),
                    "message_id": str(reflection_input.get("message_id", "") or ""),
                }
            )

        assistant_output = str(
            reflection_input.get("assistant_output", "") or reflection_input.get("output", "") or ""
        ).strip()
        if assistant_output:
            evidence.append(
                {
                    "role": "assistant",
                    "content": assistant_output,
                    "content_blocks": [{"type": "text", "text": assistant_output}],
                    "session_id": str(reflection_input.get("session_id", "") or ""),
                    "turn_id": str(reflection_input.get("turn_id", "") or ""),
                    "message_id": str(reflection_input.get("message_id", "") or ""),
                }
            )

        recent_turns = []
        metadata = reflection_input.get("metadata") if isinstance(reflection_input.get("metadata"), dict) else {}
        if isinstance(metadata.get("recent_turns"), list):
            recent_turns = list(metadata.get("recent_turns") or [])
        for item in recent_turns:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            evidence.append(
                {
                    "role": str(item.get("role", "") or "").strip() or "assistant",
                    "content": content,
                    "content_blocks": [{"type": "text", "text": content}],
                    "session_id": str(reflection_input.get("session_id", "") or ""),
                    "turn_id": str(reflection_input.get("turn_id", "") or ""),
                    "message_id": str(item.get("message_id", "") or reflection_input.get("message_id", "") or ""),
                }
            )
        return evidence[:6]

    @staticmethod
    def _extract_tool_names(reflection_input: ReflectionInput) -> list[str]:
        names: list[str] = []
        for item in list(reflection_input.get("task_trace", []) or []):
            if not isinstance(item, dict):
                continue
            containers: list[Mapping[str, Any]] = [item]
            nested = item.get("data")
            if isinstance(nested, Mapping):
                containers.append(nested)
            for container in containers:
                for key in ("tool_name", "tool", "name", "node"):
                    value = str(container.get(key, "") or "").strip()
                    if value and value not in names:
                        names.append(value)
            payload = nested.get("payload") if isinstance(nested, Mapping) else None
            if isinstance(payload, Mapping):
                value = str(payload.get("tool_name", "") or "").strip()
                if value and value not in names:
                    names.append(value)
        return names[:6]

    @staticmethod
    def _normalize_text_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
        return items

    @staticmethod
    def _normalize_trace_items(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for entry in value:
            if isinstance(entry, Mapping):
                items.append(dict(entry))
        return items

    @staticmethod
    def _normalize_turns(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        turns: list[dict[str, Any]] = []
        for entry in value:
            if not isinstance(entry, Mapping):
                continue
            role = str(entry.get("role", "") or "").strip()
            content = str(entry.get("content", "") or "").strip()
            if role and content:
                turns.append({"role": role, "content": content})
        return turns[:10]

    @staticmethod
    def _normalize_tool_usage(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for entry in value:
            if isinstance(entry, Mapping):
                items.append(dict(entry))
        return items


__all__ = ["ReflectionManager", "TurnReflectionProposal"]

