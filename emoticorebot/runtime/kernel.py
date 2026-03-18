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
from emoticorebot.output.runtime import OutputRuntime
from emoticorebot.protocol.contracts import ChannelKind, InputKind, SessionMode
from emoticorebot.protocol.envelope import BusEnvelope
from emoticorebot.protocol.events import DeliveryFailedPayload, RepliedPayload, ReplyReadyPayload
from emoticorebot.protocol.topics import EventType
from emoticorebot.reflection.runtime import ReflectionRuntime
from emoticorebot.right.runtime import RightBrainRuntime
from emoticorebot.runtime.transport_bus import TransportBus
from emoticorebot.session.runtime import SessionRuntime


@dataclass(slots=True)
class TurnReply:
    session_id: str
    turn_id: str
    reply_id: str
    content: str
    related_task_id: str | None
    event_type: str


@dataclass(slots=True)
class _OpenInputStream:
    stream_id: str
    next_chunk_index: int = 0


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
        self._right_brain_runtime = RightBrainRuntime(
            bus=self._bus,
            worker_llm=worker_llm,
            context_builder=context_builder,
            tool_registry=tool_registry,
        )
        self._session = SessionRuntime(bus=self._bus, task_store=self._right_brain_runtime.task_store)
        self._front = FrontRuntime(
            bus=self._bus,
            task_store=self._right_brain_runtime.task_store,
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
        self._pending_reply_candidates: dict[str, TurnReply] = {}
        self._pending_delivery_receipts: set[str] = set()
        self._pending_reply_ids_by_turn: dict[tuple[str, str], set[str]] = {}
        self._open_input_streams: dict[tuple[str, str], _OpenInputStream] = {}
        self._started = False

        self._session.register()
        self._right_brain_runtime.register()
        self._front.register()
        self._output_runtime = OutputRuntime(bus=self._bus)
        self._output_runtime.register()
        self._delivery_runtime = DeliveryRuntime(bus=self._bus, transport=transport, should_deliver=self._should_deliver_reply)
        self._delivery_runtime.register()
        self._reflection.register()
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_INLINE_READY, handler=self._remember_reply_candidate)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_PUSH_READY, handler=self._remember_reply_candidate)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_STREAM_OPEN, handler=self._remember_reply_candidate)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_STREAM_DELTA, handler=self._remember_reply_candidate)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_STREAM_CLOSE, handler=self._remember_reply_candidate)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_REPLIED, handler=self._capture_delivery_receipt)
        self._bus.subscribe(consumer="kernel", event_type=EventType.OUTPUT_DELIVERY_FAILED, handler=self._capture_delivery_failure)

    @property
    def task_store(self):
        return self._right_brain_runtime.task_store

    @property
    def event_bus(self) -> PriorityPubSubBus:
        return self._bus

    @property
    def session_runtime(self) -> SessionRuntime:
        return self._session

    @property
    def right_brain_runtime(self) -> RightBrainRuntime:
        return self._right_brain_runtime

    @property
    def front_runtime(self) -> FrontRuntime:
        return self._front

    @property
    def reflection_runtime(self) -> ReflectionRuntime:
        return self._reflection

    @property
    def output_runtime(self) -> OutputRuntime:
        return self._output_runtime

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
        await self._right_brain_runtime.stop()
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
            self._clear_pending_reply_state(key)
            if self._pending_turn_by_session.get(session_id) == turn_id:
                self._pending_turn_by_session.pop(session_id, None)

    async def open_user_stream(
        self,
        *,
        session_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        message_id: str,
        channel_kind: ChannelKind = "voice",
        input_kind: InputKind = "voice",
        history_context: str = "",
        metadata: dict[str, object] | None = None,
        session_mode: SessionMode | None = None,
    ) -> str:
        await self.start()
        stream_id = f"stream_{uuid4().hex[:12]}"
        key = (session_id, stream_id)
        snapshot = self._session.snapshot(session_id)
        payload_metadata = dict(metadata or {})
        if history_context:
            payload_metadata["history_context"] = history_context
        payload_metadata.setdefault("barge_in", bool(snapshot.active_reply_stream_id))
        self._open_input_streams[key] = _OpenInputStream(stream_id=stream_id)
        await self._bus.publish(
            self._input_normalizer.normalize_stream_start(
                session_id=session_id,
                stream_id=stream_id,
                channel=channel,
                chat_id=chat_id,
                sender_id=sender_id,
                message_id=message_id,
                channel_kind=channel_kind,
                input_kind=input_kind,
                metadata=payload_metadata,
                session_mode=session_mode,
            )
        )
        return stream_id

    async def append_user_stream_chunk(
        self,
        *,
        session_id: str,
        stream_id: str,
        chunk_text: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await self.start()
        state = self._require_open_input_stream(session_id=session_id, stream_id=stream_id)
        if not str(chunk_text or ""):
            return
        await self._bus.publish(
            self._input_normalizer.normalize_stream_chunk(
                session_id=session_id,
                stream_id=stream_id,
                chunk_index=state.next_chunk_index,
                chunk_text=chunk_text,
                metadata=dict(metadata or {}),
            )
        )
        state.next_chunk_index += 1

    async def commit_user_stream(
        self,
        *,
        session_id: str,
        stream_id: str,
        committed_text: str,
        history_context: str = "",
        metadata: dict[str, object] | None = None,
        timeout_s: float = 30.0,
    ) -> TurnReply:
        if self._brain_llm is None:
            raise RuntimeError("RuntimeKernel.commit_user_stream requires brain_llm")
        await self.start()
        self._require_open_input_stream(session_id=session_id, stream_id=stream_id)

        turn_id = f"turn_{uuid4().hex[:12]}"
        key = (session_id, turn_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TurnReply] = loop.create_future()
        self._pending_turns[key] = future
        self._pending_turn_by_session[session_id] = turn_id
        self._active_turn_by_session[session_id] = turn_id

        payload_metadata = dict(metadata or {})
        if history_context:
            payload_metadata["history_context"] = history_context
        await self._bus.publish(
            self._input_normalizer.normalize_stream_commit(
                session_id=session_id,
                turn_id=turn_id,
                stream_id=stream_id,
                committed_text=committed_text,
                metadata=payload_metadata,
            )
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        finally:
            self._pending_turns.pop(key, None)
            if self._pending_turn_by_session.get(session_id) == turn_id:
                self._pending_turn_by_session.pop(session_id, None)

    async def interrupt_user_stream(
        self,
        *,
        session_id: str,
        stream_id: str,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await self.start()
        self._require_open_input_stream(session_id=session_id, stream_id=stream_id)
        try:
            await self._bus.publish(
                self._input_normalizer.normalize_stream_interrupted(
                    session_id=session_id,
                    stream_id=stream_id,
                    reason=reason,
                    metadata=dict(metadata or {}),
                )
            )
        finally:
            self._open_input_streams.pop((session_id, stream_id), None)

    def latest_task_for_session(self, session_id: str, *, include_terminal: bool = True):
        return self.task_store.latest_for_session(session_id, include_terminal=include_terminal)

    def get_task(self, task_id: str):
        return self.task_store.get(task_id)

    def clear_session(self, session_id: str) -> None:
        self.task_store.remove_session(session_id)
        self._session.clear_session(session_id)
        self._pending_turn_by_session.pop(session_id, None)
        self._active_turn_by_session.pop(session_id, None)
        for key in [key for key in self._open_input_streams if key[0] == session_id]:
            self._open_input_streams.pop(key, None)

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

    async def _remember_reply_candidate(self, event: BusEnvelope[ReplyReadyPayload]) -> None:
        if not self._should_deliver_reply(event):
            return
        stream_state = self._stream_state(event.payload)
        if stream_state in {"open", "delta", "superseded"}:
            return
        key = (event.session_id or "", event.turn_id or "")
        future = self._pending_turns.get(key)
        if future is None or future.done():
            return
        content = event.payload.reply.plain_text or "\n".join(
            block.text or "" for block in event.payload.reply.content_blocks if block.type == "text"
        ).strip()
        reply_id = event.payload.reply.reply_id
        self._pending_reply_ids_by_turn.setdefault(key, set()).add(reply_id)
        self._pending_reply_candidates[reply_id] = TurnReply(
            session_id=event.session_id or "",
            turn_id=event.turn_id or "",
            reply_id=reply_id,
            content=content,
            related_task_id=event.payload.related_task_id,
            event_type=event.event_type,
        )
        if reply_id in self._pending_delivery_receipts:
            self._pending_delivery_receipts.discard(reply_id)
            self._resolve_turn_reply(reply_id=reply_id, event_type=EventType.OUTPUT_REPLIED)

    async def _capture_delivery_receipt(self, event: BusEnvelope[RepliedPayload]) -> None:
        key = (event.session_id or "", event.turn_id or "")
        reply_id = event.payload.reply_id
        future = self._pending_turns.get(key)
        if future is None or future.done():
            self._pending_reply_candidates.pop(reply_id, None)
            self._pending_delivery_receipts.discard(reply_id)
            return
        if reply_id not in self._pending_reply_candidates:
            self._pending_delivery_receipts.add(reply_id)
            return
        self._resolve_turn_reply(reply_id=reply_id, event_type=event.event_type)

    async def _capture_delivery_failure(self, event: BusEnvelope[DeliveryFailedPayload]) -> None:
        key = (event.session_id or "", event.turn_id or "")
        future = self._pending_turns.get(key)
        if future is None or future.done():
            self._pending_reply_candidates.pop(event.payload.reply_id, None)
            self._pending_delivery_receipts.discard(event.payload.reply_id)
            return
        future.set_exception(RuntimeError(f"reply delivery failed: {event.payload.reason}"))
        self._clear_pending_reply_state(key)

    def _resolve_turn_reply(self, *, reply_id: str, event_type: str) -> None:
        candidate = self._pending_reply_candidates.pop(reply_id, None)
        if candidate is None:
            return
        key = (candidate.session_id, candidate.turn_id)
        future = self._pending_turns.get(key)
        if future is None or future.done():
            self._clear_pending_reply_state(key)
            return
        future.set_result(
            TurnReply(
                session_id=candidate.session_id,
                turn_id=candidate.turn_id,
                reply_id=candidate.reply_id,
                content=candidate.content,
                related_task_id=candidate.related_task_id,
                event_type=event_type,
            )
        )
        self._clear_pending_reply_state(key)

    def _clear_pending_reply_state(self, key: tuple[str, str]) -> None:
        reply_ids = self._pending_reply_ids_by_turn.pop(key, set())
        for reply_id in reply_ids:
            self._pending_reply_candidates.pop(reply_id, None)
            self._pending_delivery_receipts.discard(reply_id)

    def _should_deliver_reply(self, event: BusEnvelope[ReplyReadyPayload]) -> bool:
        if event.event_type == EventType.OUTPUT_PUSH_READY or event.payload.delivery_mode == "push":
            return True
        session_id = str(event.session_id or "").strip()
        turn_id = str(event.turn_id or "").strip()
        if not session_id or not turn_id:
            return True
        current_turn_id = self._active_turn_by_session.get(session_id)
        if current_turn_id is None:
            return True
        return current_turn_id == turn_id

    @staticmethod
    def _stream_state(payload: ReplyReadyPayload) -> str:
        return str(payload.stream_state or "").strip()

    def _require_open_input_stream(self, *, session_id: str, stream_id: str) -> _OpenInputStream:
        key = (str(session_id or "").strip(), str(stream_id or "").strip())
        state = self._open_input_streams.get(key)
        if state is None:
            raise ValueError(f"input stream is not open: session_id={key[0]!r}, stream_id={key[1]!r}")
        return state

    def _close_context_builder(self) -> None:
        close = getattr(self._context_builder, "close", None)
        if callable(close):
            close()


__all__ = ["RuntimeKernel", "TurnReply"]
