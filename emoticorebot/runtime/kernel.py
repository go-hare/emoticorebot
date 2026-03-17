"""Bus-driven runtime kernel used by bootstrap and direct message handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from emoticorebot.bus.pubsub import PriorityPubSubBus
from emoticorebot.config.schema import MemoryConfig, ProvidersConfig
from emoticorebot.delivery.runtime import DeliveryRuntime
from emoticorebot.front.controller import FrontRuntime
from emoticorebot.io.normalizer import InputNormalizer
from emoticorebot.memory.persona import GovernedWriteResult
from emoticorebot.models.emotion_state import EmotionStateManager
from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import ReplyReadyPayload
from emoticorebot.protocol.task_models import MessageRef
from emoticorebot.protocol.topics import EventType
from emoticorebot.reflection.runtime import ReflectionRuntime
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.session.runtime import SessionRuntime
from emoticorebot.task.runtime import TaskRuntime


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
        self._context_builder = context_builder
        self._bus = PriorityPubSubBus()
        self._input_normalizer = InputNormalizer()
        self._task_runtime = TaskRuntime(
            bus=self._bus,
            worker_llm=worker_llm,
            context_builder=context_builder,
            tool_registry=tool_registry,
        )
        self._session = SessionRuntime(bus=self._bus, task_store=self._task_runtime.task_store)
        self._front = FrontRuntime(
            bus=self._bus,
            task_store=self._task_runtime.task_store,
            brain_llm=brain_llm,
            context_builder=context_builder,
            session_runtime=self._session,
        )
        self._reflection = ReflectionRuntime(
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

        self._session.register()
        self._task_runtime.register()
        self._front.register()
        self._delivery_runtime = DeliveryRuntime(bus=self._bus, transport=transport, should_deliver=self._should_deliver_reply)
        self._delivery_runtime.register()
        self._reflection.register()
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_REPLY_APPROVED, handler=self._capture_reply)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_REPLY_REDACTED, handler=self._capture_reply)

    @property
    def task_store(self):
        return self._task_runtime.task_store

    @property
    def event_bus(self) -> PriorityPubSubBus:
        return self._bus

    @property
    def session_runtime(self) -> SessionRuntime:
        return self._session

    @property
    def task_runtime(self) -> TaskRuntime:
        return self._task_runtime

    @property
    def front_runtime(self) -> FrontRuntime:
        return self._front

    @property
    def reflection_runtime(self) -> ReflectionRuntime:
        return self._reflection

    @property
    def delivery_runtime(self) -> DeliveryRuntime:
        return self._delivery_runtime

    async def start(self) -> None:
        if self._started:
            return
        await self._bus.start()
        await self._reflection.start()
        self._started = True

    async def stop(self) -> None:
        await self._front.stop()
        await self._task_runtime.stop()
        await self._reflection.stop()
        await self._bus.stop()
        self._close_context_builder()
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
        barge_in = bool(self._session.snapshot(session_id).active_reply_stream_id)

        payload_metadata = dict(metadata or {})
        payload_metadata["history_context"] = history_context
        envelope = self._input_normalizer.normalize_text_message(
            session_id=session_id,
            turn_id=turn_id,
            channel=channel,
            chat_id=chat_id,
            sender_id=sender_id,
            message_id=message_id,
            content=content,
            attachments=attachments,
            metadata=payload_metadata,
            barge_in=barge_in,
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
        self._session.clear_session(session_id)
        self._pending_turn_by_session.pop(session_id, None)
        self._active_turn_by_session.pop(session_id, None)

    def is_current_turn(self, *, session_id: str, turn_id: str | None) -> bool:
        current_turn_id = self._active_turn_by_session.get(session_id)
        return bool(turn_id) and current_turn_id == turn_id

    async def run_deep_reflection(self, *, reason: str = "", warm_limit: int = 15):
        await self.start()
        return await self._reflection.run_deep_reflection(reason=reason, warm_limit=warm_limit)

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
        return await self._reflection.rollback_anchor(
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
        stream_state = str(event.payload.reply.metadata.get("stream_state", "") or "").strip()
        if stream_state in {"open", "delta", "superseded"}:
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

    def _close_context_builder(self) -> None:
        close = getattr(self._context_builder, "close", None)
        if callable(close):
            close()


__all__ = ["RuntimeKernel", "TurnReply"]
