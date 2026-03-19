"""Reflection governor for the v3 event graph."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from weakref import ref

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.memory.store import MemoryStore
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import SystemSignalPayload
from emoticorebot.protocol.reflection_models import (
    ReflectionUpdatePayload,
    ReflectionWriteCommittedPayload,
    ReflectionWriteRequestPayload,
    ReflectionSignalPayload,
)
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType

from .deep import DeepReflectionResult, DeepReflectionService
from .manager import ReflectionManager
from .persona import GovernedWriteResult, ManagedAnchorWriter, PersonaManager
from .turn import TurnReflectionService


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ReflectionGovernor:
    """Owns reflection orchestration and memory governance."""

    _SYSTEM_SESSION_ID = "system:memory"
    _MAX_CONTEXT_IDS = 8
    _MAX_CONTEXT_BUCKETS = 128
    _MAX_PROCESSED_REFLECTION_TRIGGERS = 512

    def __init__(
        self,
        *,
        bus: PriorityPubSubBus,
        workspace: Path,
        emotion_manager: EmotionStateManager | None = None,
        reflection_llm: Any = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ) -> None:
        self._bus = bus
        self._workspace = Path(workspace)
        self._emotion_mgr = emotion_manager or EmotionStateManager(self._workspace)
        self._memory_store = MemoryStore(
            self._workspace,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        deep_reflection = DeepReflectionService(reflection_llm)
        self._persona = PersonaManager(
            emotion_manager=self._emotion_mgr,
            anchor_writer=ManagedAnchorWriter(self._workspace),
        )
        self._reflection = ReflectionManager(
            workspace=self._workspace,
            emotion_manager=self._emotion_mgr,
            memory_store=self._memory_store,
            turn_reflection=TurnReflectionService(self._emotion_mgr, reflection_llm),
            deep_reflection=deep_reflection,
        )
        self._processed_reflection_triggers: set[str] = set()
        self._processed_reflection_trigger_order: deque[str] = deque()
        self._recent_context_ids: OrderedDict[str, deque[str]] = OrderedDict()

    def register(self) -> None:
        self._bus.subscribe(
            consumer="reflection_governor",
            event_type=EventType.REFLECTION_LIGHT,
            handler=self._weak_handler("_on_reflection_signal"),
        )
        self._bus.subscribe(
            consumer="reflection_governor",
            event_type=EventType.REFLECTION_DEEP,
            handler=self._weak_handler("_on_reflection_signal"),
        )
        self._bus.subscribe(
            consumer="reflection_governor",
            event_type=EventType.REFLECTION_WRITE_REQUEST,
            handler=self._weak_handler("_on_write_request"),
        )

    def close(self) -> None:
        self._memory_store.close()

    @property
    def persona(self) -> PersonaManager:
        return self._persona

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def _weak_handler(self, method_name: str):
        weak_self = ref(self)

        async def _handler(event: BusEnvelope[ProtocolModel]) -> None:
            current = weak_self()
            if current is None:
                return None
            method = getattr(current, method_name)
            await method(event)

        return _handler

    async def run_deep_reflection(
        self,
        *,
        reason: str = "",
        warm_limit: int = 15,
    ) -> DeepReflectionResult:
        events = self._reflection.recent_cognitive_events(limit=max(6, warm_limit))
        if not events:
            return DeepReflectionResult()
        return await self._apply_deep_reflection(
            reason=reason or "periodic_signal",
            session_id=self._SYSTEM_SESSION_ID,
            turn_id="turn_background_reflection",
            correlation_id="background_reflection",
            causation_id=None,
            task_id=None,
            recent_context_ids=[],
            metadata={},
            events=events,
        )

    async def rollback_anchor(
        self,
        *,
        target: str,
        scope: str = "deep",
        version: int | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        correlation_id: str | None = None,
        reason: str = "manual_rollback",
    ) -> GovernedWriteResult:
        result = self._persona.rollback_updates(target, scope=scope, version=version)
        if not result.applied:
            return result

        signal = self._system_like_signal(
            reason=reason,
            session_id=session_id or self._SYSTEM_SESSION_ID,
            turn_id=turn_id,
            task_id=None,
            correlation_id=correlation_id or f"{target}:{scope}",
            causation_id=None,
            recent_context_ids=[],
            metadata={"source": "governance_admin", "target": target, "scope": scope},
        )
        await self._publish_update_event(
            signal=signal,
            target=target,
            updates=self._persona.current_updates(target, scope=scope),
            source_memory_ids=[],
            metadata={
                "source": "governance_admin",
                "reason": reason,
                **self._governance_metadata(result, scope=scope, action="rollback"),
            },
        )
        return result

    async def _on_reflection_signal(self, event: BusEnvelope[ReflectionSignalPayload]) -> None:
        if self._is_processed_reflection_trigger(event.payload.trigger_id):
            return

        self._remember_reflection_trigger(event.payload.trigger_id)
        if event.event_type == EventType.REFLECTION_LIGHT:
            await self._apply_turn_reflection(event)
            return
        if event.event_type == EventType.REFLECTION_DEEP:
            warm_limit = self._deep_warm_limit(event)
            await self._apply_deep_reflection(
                reason=str(event.payload.reason or "deep_reflection"),
                session_id=event.session_id or self._SYSTEM_SESSION_ID,
                turn_id=event.turn_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id or event.event_id,
                causation_id=event.event_id,
                task_id=event.task_id,
                recent_context_ids=self._context_ids_for(event),
                metadata=dict(event.payload.metadata or {}),
                events=self._reflection.recent_cognitive_events(limit=max(6, warm_limit)),
            )

    async def _apply_turn_reflection(
        self,
        signal: BusEnvelope[ReflectionSignalPayload],
    ) -> None:
        proposal = await self._reflection.propose_turn(signal=signal)
        if proposal is None:
            await self._publish_warning(signal, reason="reflection_input_missing_content")
            return

        self._remember(signal.payload.source_event_id, session_id=signal.session_id, task_id=signal.task_id)
        event_ids = self._reflection.append_turn_events(proposal)
        for event_id in event_ids:
            self._remember(event_id, session_id=signal.session_id, task_id=signal.task_id)

        memory_ids = self._reflection.append_turn_memories(proposal, event_ids=event_ids)
        committed = self._reflection.lookup_memory_records(memory_ids)
        await self._publish_committed_records(
            signal=signal,
            records=committed,
            metadata={"reflection_type": "turn", "trigger_id": signal.payload.trigger_id},
        )

        updated_user, updated_soul, _, _ = self._persona.apply_turn_reflection_results(proposal.turn_reflection)
        if updated_user.applied:
            await self._publish_update_event(
                signal=signal,
                target="user_model",
                updates=proposal.turn_reflection.get("user_updates"),
                source_memory_ids=memory_ids,
                metadata={
                    "reflection_type": "turn",
                    "trigger_id": signal.payload.trigger_id,
                    **self._governance_metadata(updated_user, scope="turn", action="apply"),
                },
            )
        if updated_soul.applied:
            await self._publish_update_event(
                signal=signal,
                target="persona",
                updates=proposal.turn_reflection.get("soul_updates"),
                source_memory_ids=memory_ids,
                metadata={
                    "reflection_type": "turn",
                    "trigger_id": signal.payload.trigger_id,
                    **self._governance_metadata(updated_soul, scope="turn", action="apply"),
                },
            )

    async def _apply_deep_reflection(
        self,
        *,
        reason: str,
        session_id: str,
        turn_id: str | None,
        correlation_id: str | None,
        causation_id: str | None,
        task_id: str | None,
        recent_context_ids: list[str],
        metadata: dict[str, Any],
        events: list[dict[str, Any]] | None = None,
    ) -> DeepReflectionResult:
        recent_events = events or self._reflection.recent_cognitive_events(limit=15)
        if not recent_events:
            return DeepReflectionResult()

        proposal = await self._reflection.propose_deep(events=recent_events)
        result = self._reflection.append_deep_memories(proposal)

        signal = self._system_like_signal(
            reason=reason,
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            recent_context_ids=recent_context_ids,
            metadata=metadata,
        )
        committed = self._reflection.lookup_memory_records(result.memory_ids)
        await self._publish_committed_records(
            signal=signal,
            records=committed,
            metadata={"reflection_type": "deep", "reason": reason, **metadata},
        )

        updated_user = self._persona.apply_updates_result("user_model", result.user_updates, scope="deep")
        updated_soul = self._persona.apply_updates_result("persona", result.soul_updates, scope="deep")
        if updated_user.applied:
            await self._publish_update_event(
                signal=signal,
                target="user_model",
                updates=result.user_updates,
                source_memory_ids=result.memory_ids,
                metadata={
                    "reflection_type": "deep",
                    "reason": reason,
                    **metadata,
                    **self._governance_metadata(updated_user, scope="deep", action="apply"),
                },
            )
        if updated_soul.applied:
            await self._publish_update_event(
                signal=signal,
                target="persona",
                updates=result.soul_updates,
                source_memory_ids=result.memory_ids,
                metadata={
                    "reflection_type": "deep",
                    "reason": reason,
                    **metadata,
                    **self._governance_metadata(updated_soul, scope="deep", action="apply"),
                },
            )
        return replace(result, updated_user=updated_user.applied, updated_soul=updated_soul.applied)

    async def _on_write_request(self, event: BusEnvelope[ReflectionWriteRequestPayload]) -> None:
        payload = event.payload
        if not (payload.summary or payload.content):
            await self._publish_warning(event, reason="reflection_write_missing_content")
            return

        record = self._record_from_write_request(event)
        memory_ids = self._memory_store.append_many([record])
        committed = self._reflection.lookup_memory_records(memory_ids)
        await self._publish_committed_records(signal=event, records=committed, metadata={"source": "write_request"})

        summary = payload.summary or payload.content or ""
        writer_result = GovernedWriteResult(applied=False)
        if payload.memory_type in {"persona", "user_model"}:
            writer_result = self._persona.apply_updates_result(
                payload.memory_type,
                [summary],
                scope="deep",
            )
        if payload.memory_type in {"persona", "user_model"} and writer_result.applied:
            await self._publish_update_event(
                signal=event,
                target=payload.memory_type,
                updates=[summary],
                source_memory_ids=memory_ids,
                metadata={
                    "request_id": payload.request_id,
                    **payload.metadata,
                    **self._governance_metadata(writer_result, scope="deep", action="apply"),
                },
            )

    async def _remember_event(self, event: BusEnvelope[ProtocolModel]) -> None:
        self._remember(event.event_id, session_id=event.session_id, task_id=event.task_id)

    async def _publish_committed_records(
        self,
        *,
        signal: BusEnvelope[ProtocolModel],
        records: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        for record in records:
            memory_id = str(record.get("memory_id", "") or "").strip()
            if not memory_id:
                continue
            metadata_payload = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            await self._bus.publish(
                build_envelope(
                    event_type=EventType.REFLECTION_WRITE_COMMITTED,
                    source="reflection_governor",
                    target="broadcast",
                    session_id=signal.session_id or self._SYSTEM_SESSION_ID,
                    turn_id=signal.turn_id,
                    task_id=signal.task_id,
                    correlation_id=signal.correlation_id or signal.task_id or signal.turn_id,
                    causation_id=signal.event_id,
                    payload=ReflectionWriteCommittedPayload(
                        request_id=str(metadata_payload.get("request_id", "") or memory_id or _new_id("memreq")),
                        memory_id=memory_id,
                        memory_type=self._record_memory_type(record),
                        committed_at=_utc_now(),
                        metadata={
                            **metadata,
                            "record_type": str(record.get("memory_type", "") or ""),
                            "record_subtype": str(metadata_payload.get("subtype", "") or ""),
                        },
                    ),
                )
            )

    async def _publish_update_event(
        self,
        *,
        signal: BusEnvelope[ProtocolModel],
        target: str,
        updates: Any,
        source_memory_ids: list[str],
        metadata: dict[str, Any],
    ) -> None:
        normalized = PersonaManager.normalize_update_lines(updates)
        if not normalized:
            return
        event_type = EventType.REFLECTION_UPDATE_PERSONA if target == "persona" else EventType.REFLECTION_UPDATE_USER_MODEL
        await self._bus.publish(
            build_envelope(
                event_type=event_type,
                source="reflection_governor",
                target="broadcast",
                session_id=signal.session_id or self._SYSTEM_SESSION_ID,
                turn_id=signal.turn_id,
                task_id=signal.task_id,
                correlation_id=signal.correlation_id or signal.task_id or signal.turn_id,
                causation_id=signal.event_id,
                payload=ReflectionUpdatePayload(
                    update_id=_new_id("memupd"),
                    target=target,
                    summary=normalized[0],
                    content="\n".join(f"- {item}" for item in normalized),
                    confidence=0.86,
                    source_memory_ids=list(source_memory_ids),
                    metadata=metadata,
                ),
            )
        )

    async def _publish_warning(self, event: BusEnvelope[ProtocolModel], *, reason: str) -> None:
        await self._bus.publish(
            build_envelope(
                event_type=EventType.SYSTEM_WARNING,
                source="reflection_governor",
                target="broadcast",
                session_id=event.session_id or self._SYSTEM_SESSION_ID,
                turn_id=event.turn_id,
                task_id=event.task_id,
                correlation_id=event.correlation_id or event.task_id or event.turn_id,
                causation_id=event.event_id,
                payload=SystemSignalPayload(
                    signal_id=_new_id("signal"),
                    signal_type="warning",
                    reason=reason,
                    related_event_id=event.event_id,
                    related_task_id=event.task_id,
                    severity="warning",
                ),
            )
        )

    def _record_from_write_request(self, event: BusEnvelope[ReflectionWriteRequestPayload]) -> dict[str, Any]:
        payload = event.payload
        memory_type, subtype = self._memory_record_shape(payload.memory_type)
        return {
            "memory_id": f"mem_{payload.request_id}",
            "memory_type": memory_type,
            "session_id": event.session_id or self._SYSTEM_SESSION_ID,
            "summary": payload.summary or payload.content or "",
            "detail": payload.content or payload.summary or "",
            "confidence": payload.confidence or 0.8,
            "stability": 0.9 if payload.memory_type in {"persona", "user_model"} else 0.65,
            "source_module": payload.source_component or event.source,
            "source_event_ids": [
                *self._context_ids_for(event),
                *[item for item in payload.evidence_event_ids if item not in self._context_ids_for(event)],
            ],
            "metadata": {"request_id": payload.request_id, "subtype": subtype, **payload.metadata},
        }

    @staticmethod
    def _memory_record_shape(memory_type: str) -> tuple[str, str]:
        mapping = {
            "persona": ("reflection", "persona"),
            "user_model": ("relationship", "user_model"),
            "episodic": ("reflection", "turn_insight"),
            "task_experience": ("execution", "workflow"),
            "tool_experience": ("execution", "tool_experience"),
        }
        return mapping.get(memory_type, ("fact", "generic"))

    @staticmethod
    def _record_memory_type(record: Mapping[str, Any]) -> str:
        memory_type = str(record.get("memory_type", "") or "").strip()
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        record_subtype = str(metadata.get("subtype", "") or "").strip()
        if record_subtype == "persona":
            return "persona"
        if memory_type == "relationship":
            return "user_model"
        if record_subtype == "tool_experience":
            return "tool_experience"
        if memory_type == "execution":
            return "task_experience"
        return "episodic"

    def _context_ids_for(self, event: BusEnvelope[ProtocolModel]) -> list[str]:
        payload = getattr(event, "payload", None)
        signal_context = list(getattr(payload, "recent_context_ids", []) or [])
        source_event_id = str(getattr(payload, "source_event_id", "") or "").strip()
        merged: list[str] = []
        for event_id in [*signal_context, source_event_id, *self._recent_for(event)]:
            if event_id and event_id not in merged:
                merged.append(event_id)
        return merged[: self._MAX_CONTEXT_IDS]

    def _recent_for(self, event: BusEnvelope[ProtocolModel]) -> list[str]:
        key = self._context_key(session_id=event.session_id, task_id=event.task_id)
        if key is None:
            return []
        bucket = self._recent_context_ids.get(key)
        if bucket is None:
            return []
        self._recent_context_ids.move_to_end(key)
        return list(bucket)

    def _remember(self, event_id: str | None, *, session_id: str | None, task_id: str | None) -> None:
        if not event_id:
            return
        key = self._context_key(session_id=session_id, task_id=task_id)
        if key is None:
            return
        bucket = self._recent_context_ids.get(key)
        if bucket is None:
            bucket = deque(maxlen=self._MAX_CONTEXT_IDS)
            self._recent_context_ids[key] = bucket
            self._trim_context_buckets()
        else:
            self._recent_context_ids.move_to_end(key)
        if event_id in bucket:
            return
        bucket.append(event_id)

    def _is_processed_reflection_trigger(self, trigger_id: str | None) -> bool:
        return bool(trigger_id) and trigger_id in self._processed_reflection_triggers

    def _remember_reflection_trigger(self, trigger_id: str | None) -> None:
        if not trigger_id or trigger_id in self._processed_reflection_triggers:
            return
        self._processed_reflection_triggers.add(trigger_id)
        self._processed_reflection_trigger_order.append(trigger_id)
        while len(self._processed_reflection_trigger_order) > self._MAX_PROCESSED_REFLECTION_TRIGGERS:
            evicted = self._processed_reflection_trigger_order.popleft()
            self._processed_reflection_triggers.discard(evicted)

    def _trim_context_buckets(self) -> None:
        while len(self._recent_context_ids) > self._MAX_CONTEXT_BUCKETS:
            self._recent_context_ids.popitem(last=False)

    @staticmethod
    def _deep_warm_limit(signal: BusEnvelope[ReflectionSignalPayload]) -> int:
        metadata = dict(signal.payload.metadata or {})
        try:
            return max(int(metadata.get("warm_limit", 15) or 15), 1)
        except (TypeError, ValueError):
            return 15

    @staticmethod
    def _governance_metadata(result: GovernedWriteResult, *, scope: str, action: str) -> dict[str, Any]:
        if not result.applied:
            return {}
        return {
            "governance": {
                "action": action,
                "scope": scope,
                "version": result.version,
                "conflict_detected": result.conflict_detected,
                "rollback_to_version": result.rollback_to_version,
                "snapshot_path": str(result.snapshot_path) if result.snapshot_path is not None else None,
            }
        }

    @staticmethod
    def _context_key(*, session_id: str | None, task_id: str | None) -> str | None:
        if task_id:
            return f"task:{task_id}"
        if session_id:
            return f"session:{session_id}"
        return None

    @staticmethod
    def _system_like_signal(
        *,
        reason: str,
        session_id: str,
        turn_id: str | None,
        task_id: str | None,
        correlation_id: str | None,
        causation_id: str | None,
        recent_context_ids: list[str],
        metadata: dict[str, Any],
    ) -> BusEnvelope[ReflectionSignalPayload]:
        return build_envelope(
            event_type=EventType.REFLECTION_DEEP,
            source="reflection_governor",
            target="reflection_governor",
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            correlation_id=correlation_id or turn_id or task_id or "background_reflection",
            causation_id=causation_id,
            payload=ReflectionSignalPayload(
                trigger_id=_new_id("reflect"),
                reason=reason,
                task_id=task_id,
                recent_context_ids=list(recent_context_ids),
                metadata=dict(metadata),
            ),
        )


__all__ = ["ReflectionGovernor"]





