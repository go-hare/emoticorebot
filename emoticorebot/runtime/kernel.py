"""Bus-driven runtime kernel used by bootstrap and direct message handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from emoticorebot.brain.executive import ExecutiveBrain
from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.delivery.service import DeliveryService
from emoticorebot.execution.team import AgentTeam
from emoticorebot.memory.governor import MemoryGovernor
from emoticorebot.memory.persona import GovernedWriteResult
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import InterruptPayload, ReplyReadyPayload, UserMessagePayload
from emoticorebot.protocol.task_models import ContentBlock, MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.runtime.service import RuntimeService
from emoticorebot.safety.guard import SafetyGuard


@dataclass(slots=True)
class TurnReply:
    session_id: str
    turn_id: str
    reply_id: str
    content: str
    related_task_id: str | None
    event_type: str


class RuntimeKernel:
    """Owns the documented brain/runtime/agent/safety/delivery event graph."""

    def __init__(
        self,
        *,
        workspace: Path,
        transport: TransportBus | None = None,
        brain_llm: object | None = None,
        worker_llm: object | None = None,
        reflection_llm: object | None = None,
        context_builder: object | None = None,
        tool_registry: object | None = None,
        emotion_manager: EmotionStateManager | None = None,
        memory_config: MemoryConfig | None = None,
        providers_config: ProvidersConfig | None = None,
    ) -> None:
        self._brain_llm = brain_llm
        self._bus = PriorityPubSubBus()
        self._runtime = RuntimeService(bus=self._bus)
        self._brain = ExecutiveBrain(
            bus=self._bus,
            task_store=self._runtime.scheduler.task_store,
            brain_llm=brain_llm,
            context_builder=context_builder,
        )
        self._team = AgentTeam(
            bus=self._bus,
            task_store=self._runtime.scheduler.task_store,
            worker_llm=worker_llm,
            context_builder=context_builder,
            tool_registry=tool_registry,
        )
        self._guard = SafetyGuard(bus=self._bus)
        self._memory = MemoryGovernor(
            bus=self._bus,
            workspace=workspace,
            emotion_manager=emotion_manager,
            reflection_llm=reflection_llm,
            memory_config=memory_config,
            providers_config=providers_config,
        )
        self._pending_turns: dict[tuple[str, str], asyncio.Future[TurnReply]] = {}
        self._pending_turn_by_session: dict[str, str] = {}
        self._active_turn_by_session: dict[str, str] = {}
        self._started = False

        self._runtime.register()
        self._brain.register()
        self._team.register()
        self._guard.register()
        self._delivery = DeliveryService(bus=self._bus, transport=transport, should_deliver=self._should_deliver_reply)
        self._delivery.register()
        self._memory.register()
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_REPLY_APPROVED, handler=self._capture_reply)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=self._capture_reply)

    @property
    def task_store(self):
        return self._runtime.scheduler.task_store

    async def start(self) -> None:
        if self._started:
            return
        await self._bus.start()
        self._started = True

    async def stop(self) -> None:
        await self._brain.stop()
        await self._team.stop()
        await self._bus.stop()
        self._started = False

    async def handle_user_message(
        self,
        *,
        session_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        content: str,
        history_context: str = "",
        attachments: list[str] | None = None,
        metadata: dict[str, object] | None = None,
        timeout_s: float = 30.0,
    ) -> TurnReply:
        if self._brain_llm is None:
            raise RuntimeError("RuntimeKernel.handle_user_message requires brain_llm")
        await self.start()
        turn_id = f"turn_{uuid4().hex[:12]}"
        key = (session_id, turn_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TurnReply] = loop.create_future()
        self._pending_turns[key] = future
        self._pending_turn_by_session[session_id] = turn_id
        self._active_turn_by_session[session_id] = turn_id

        attachment_blocks = [
            ContentBlock(type="file", path=path, name=path.rsplit("/", 1)[-1])
            for path in list(attachments or [])
            if str(path or "").strip()
        ]
        payload_metadata = dict(metadata or {})
        payload_metadata["history_context"] = history_context
        envelope = build_envelope(
            event_type=EventType.INPUT_USER_MESSAGE,
            source="gateway",
            target="brain",
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=turn_id,
            payload=UserMessagePayload(
                message=MessageRef(
                    channel=channel,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    message_id=message_id,
                ),
                plain_text=content,
                attachments=attachment_blocks,
                metadata=payload_metadata,
            ),
        )
        await self._bus.publish(envelope)
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._pending_turns.pop(key, None)
            if self._pending_turn_by_session.get(session_id) == turn_id:
                self._pending_turn_by_session.pop(session_id, None)

    def latest_task_for_session(self, session_id: str, *, include_terminal: bool = True):
        return self.task_store.latest_for_session(session_id, include_terminal=include_terminal)

    def get_task(self, task_id: str):
        return self.task_store.get(task_id)

    def clear_session(self, session_id: str) -> None:
        self.task_store.remove_session(session_id)
        self._pending_turn_by_session.pop(session_id, None)
        self._active_turn_by_session.pop(session_id, None)

    def is_current_turn(self, *, session_id: str, turn_id: str | None) -> bool:
        current_turn_id = self._active_turn_by_session.get(session_id)
        return bool(turn_id) and current_turn_id == turn_id

    async def interrupt_session(
        self,
        *,
        session_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        current_turn_id = self._active_turn_by_session.get(session_id)
        if not current_turn_id:
            return False

        latest_task = self.task_store.latest_for_session(session_id, include_terminal=False)
        pending_turn_id = self._pending_turn_by_session.get(session_id)
        has_live_turn = pending_turn_id == current_turn_id
        has_live_task = latest_task is not None and latest_task.turn_id == current_turn_id
        if not has_live_turn and not has_live_task:
            return False

        await self.start()
        await self._bus.publish(
            build_envelope(
                event_type=EventType.INPUT_INTERRUPT,
                source="gateway",
                target="broadcast",
                session_id=session_id,
                turn_id=current_turn_id,
                task_id=latest_task.task_id if has_live_task else None,
                correlation_id=(latest_task.task_id if has_live_task else current_turn_id) or None,
                payload=InterruptPayload(
                    message=MessageRef(
                        channel=channel,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        message_id=message_id,
                    ),
                    interrupt_type="new_user_message",
                    plain_text=content,
                    target_task_id=latest_task.task_id if has_live_task else None,
                    urgent=True,
                    metadata=dict(metadata or {}),
                ),
            )
        )
        return True

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15):
        await self.start()
        return await self._memory.run_deep_reflection(reason=reason, warm_limit=warm_limit)

    async def rollback_persona(
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
        await self.start()
        return await self._memory.rollback_anchor(
            target=target,
            scope=scope,
            version=version,
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=correlation_id,
            reason=reason,
        )

    async def _capture_reply(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        if not self._should_deliver_reply(event):
            return
        if str(event.payload.reply.metadata.get("stream_state", "") or "").strip() == "delta":
            return
        key = (event.session_id or "", event.turn_id or "")
        future = self._pending_turns.get(key)
        if future is None or future.done():
            return
        content = event.payload.reply.plain_text or "\n".join(
            block.text or "" for block in event.payload.reply.content_blocks if block.type == "text"
        ).strip()
        future.set_result(
            TurnReply(
                session_id=event.session_id or "",
                turn_id=event.turn_id or "",
                reply_id=event.payload.reply.reply_id,
                content=content,
                related_task_id=event.payload.related_task_id,
                event_type=event.event_type,
            )
        )

    def _should_deliver_reply(self, event: BusEnvelope[ReplyReadyPayload]) -> bool:
        session_id = str(event.session_id or "").strip()
        turn_id = str(event.turn_id or "").strip()
        if not session_id or not turn_id:
            return True
        current_turn_id = self._active_turn_by_session.get(session_id)
        if current_turn_id is None:
            return True
        return current_turn_id == turn_id


__all__ = ["RuntimeKernel", "TurnReply"]
