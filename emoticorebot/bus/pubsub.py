"""Priority pub/sub bus with topic routing, dedupe, and interceptor support."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from emoticorebot.protocol.envelope import BusEnvelope, build_envelope
from emoticorebot.protocol.events import SystemSignalPayload
from emoticorebot.protocol.task_models import ProtocolModel
from emoticorebot.protocol.topics import EventType, Topic

from .backpressure import BackpressureController, BackpressureError
from .dedupe import DedupeCache
from .interceptor import InterceptorAction, InterceptorChain, InterceptorHandler
from .priority_queue import PriorityEventQueue
from .router import EventRouter
from .subscriptions import SubscriberHandler


class PriorityPubSubBus:
    """Single physical bus used by the v3 runtime."""

    _MAX_REASON_LENGTH = 240

    def __init__(
        self,
        *,
        queue: PriorityEventQueue | None = None,
        router: EventRouter | None = None,
        interceptors: InterceptorChain | None = None,
        dedupe: DedupeCache | None = None,
        backpressure: BackpressureController | None = None,
    ) -> None:
        self._queue = queue or PriorityEventQueue()
        self._router = router or EventRouter()
        self._interceptors = interceptors or InterceptorChain()
        self._dedupe = dedupe or DedupeCache()
        self._backpressure = backpressure or BackpressureController()
        self._pump: asyncio.Task[None] | None = None

    def subscribe(
        self,
        *,
        consumer: str,
        handler: SubscriberHandler,
        topic: str | None = None,
        event_type: str | None = None,
    ) -> None:
        self._router.subscribe(consumer=consumer, handler=handler, topic=topic, event_type=event_type)

    def register_interceptor(self, *, topic: str, handler: InterceptorHandler, order: int = 100) -> None:
        self._interceptors.register(topic=topic, handler=handler, order=order)

    def qsize(self) -> int:
        return self._queue.qsize()

    async def publish(self, event: BusEnvelope[ProtocolModel]) -> bool:
        if not self._dedupe.remember(event.dedupe_key):
            return False

        should_warn = False
        if event.event_type != EventType.SYSTEM_BACKPRESSURE:
            should_warn = self._backpressure.check(self._queue.qsize() + 1)

        await self._queue.put(event)

        if should_warn:
            await self._queue.put(self._build_backpressure_event(queue_size=self._queue.qsize()))
        return True

    async def dispatch_next(self) -> None:
        event = await self._queue.get()
        outcome = await self._interceptors.run(event)

        for audit_event in outcome.audit_events:
            await self.publish(audit_event)

        if outcome.action is InterceptorAction.BLOCK:
            return

        subscribers = self._router.match(outcome.event)
        if not subscribers:
            return
        results = await asyncio.gather(
            *(subscription.handler(outcome.event) for subscription in subscribers),
            return_exceptions=True,
        )
        for subscription, result in zip(subscribers, results, strict=False):
            if not isinstance(result, Exception):
                continue
            await self.publish(self._build_subscriber_warning(event=outcome.event, consumer=subscription.consumer, error=result))

    async def drain(self) -> None:
        while not self._queue.empty():
            await self.dispatch_next()

    async def start(self) -> None:
        if self._pump is not None and not self._pump.done():
            return
        self._pump = asyncio.create_task(self._run(), name="priority-pubsub-bus")

    async def stop(self) -> None:
        if self._pump is None:
            return
        self._pump.cancel()
        try:
            await self._pump
        except asyncio.CancelledError:
            pass
        self._pump = None

    async def _run(self) -> None:
        while True:
            try:
                await self.dispatch_next()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.publish(self._build_dispatch_warning(exc))

    def _build_backpressure_event(self, *, queue_size: int) -> BusEnvelope[SystemSignalPayload]:
        payload = SystemSignalPayload(
            signal_id=f"signal_backpressure_{queue_size}",
            signal_type="backpressure",
            reason=f"bus queue size reached {queue_size}",
            severity="warning",
        )
        return build_envelope(
            event_type=EventType.SYSTEM_BACKPRESSURE,
            source="bus",
            target="broadcast",
            payload=payload,
            dedupe_key=f"backpressure:{queue_size}",
        )

    def _build_subscriber_warning(
        self,
        *,
        event: BusEnvelope[ProtocolModel],
        consumer: str,
        error: Exception,
    ) -> BusEnvelope[SystemSignalPayload]:
        return build_envelope(
            event_type=EventType.SYSTEM_HEALTH_WARNING,
            source="bus",
            target="broadcast",
            session_id=event.session_id,
            turn_id=event.turn_id,
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            payload=SystemSignalPayload(
                signal_id=f"signal_subscriber_error_{consumer}_{event.event_id[-8:]}",
                signal_type="health_warning",
                reason=self._truncate_reason(f"subscriber {consumer} failed: {type(error).__name__}: {error}"),
                related_event_id=event.event_id,
                related_task_id=event.task_id,
                severity="warning",
            ),
            dedupe_key=f"subscriber-error:{event.event_id}:{consumer}",
        )

    def _build_dispatch_warning(self, error: Exception) -> BusEnvelope[SystemSignalPayload]:
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        return build_envelope(
            event_type=EventType.SYSTEM_HEALTH_WARNING,
            source="bus",
            target="broadcast",
            payload=SystemSignalPayload(
                signal_id=f"signal_bus_dispatch_{timestamp}",
                signal_type="health_warning",
                reason=self._truncate_reason(f"bus dispatch failed: {type(error).__name__}: {error}"),
                severity="warning",
            ),
            dedupe_key=f"bus-dispatch:{type(error).__name__}:{timestamp}",
        )

    def _truncate_reason(self, value: str) -> str:
        text = str(value or "").strip()
        if len(text) <= self._MAX_REASON_LENGTH:
            return text
        return f"{text[: self._MAX_REASON_LENGTH - 3]}..."


__all__ = ["BackpressureError", "PriorityPubSubBus"]
